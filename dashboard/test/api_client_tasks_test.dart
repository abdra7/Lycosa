import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';

const base = 'http://controller:8000';

Map<String, dynamic> taskJson() => {
      'id': 't1',
      'type': 'general',
      'status': 'succeeded',
      'payload': {'prompt': 'say hi', 'model': null, 'options': {}},
      'result': {'output': 'hello!', 'model': 'llama3:8b', 'node': 'n1'},
      'error': null,
      'node_id': 'n1',
      'queued_at': '2026-07-05T10:00:00Z',
      'finished_at': '2026-07-05T10:00:05Z',
      'executions': [
        {
          'id': 'e1',
          'node_id': 'n1',
          'attempt': 1,
          'status': 'succeeded',
          'output': 'hello!',
          'error': null,
          'started_at': '2026-07-05T10:00:00Z',
          'finished_at': '2026-07-05T10:00:05Z',
        }
      ],
    };

Map<String, dynamic> runJson({String status = 'paused'}) => {
      'id': 'r1',
      'workflow_id': 'w1',
      'status': status,
      'input': 'release notes',
      'context': {'steps': {}},
      'current_step': status == 'paused' ? 'gate' : null,
      'error': null,
      'started_at': '2026-07-05T10:00:00Z',
      'finished_at': null,
      'step_runs': [
        {
          'id': 's1',
          'step_id': 'draft',
          'kind': 'task',
          'status': 'succeeded',
          'attempt': 1,
          'task_id': 't1',
          'output': 'the draft',
          'error': null,
          'started_at': '2026-07-05T10:00:00Z',
          'finished_at': '2026-07-05T10:00:03Z',
        },
        {
          'id': 's2',
          'step_id': 'gate',
          'kind': 'approval',
          'status': status == 'paused' ? 'pending_approval' : 'succeeded',
          'attempt': 1,
          'task_id': null,
          'output': 'Review the draft',
          'error': null,
          'started_at': '2026-07-05T10:00:03Z',
          'finished_at': null,
        },
      ],
    };

ApiClient client(MockClient mock) =>
    ApiClient(baseUrl: base, token: 't', httpClient: mock);

void main() {
  test('submitTask posts the payload and parses the finished task', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(jsonEncode(taskJson()), 201);
    });

    final task = await client(mock).submitTask(
        prompt: 'say hi', knowledgeQuery: 'greetings');

    expect(jsonDecode(captured.body),
        {'prompt': 'say hi', 'knowledge_query': 'greetings'});
    expect(task.status, 'succeeded');
    expect(task.output, 'hello!');
    expect(task.executions.single.attempt, 1);
  });

  test('getRun parses step runs and pause state', () async {
    final mock =
        MockClient((_) async => http.Response(jsonEncode(runJson()), 200));

    final run = await client(mock).getRun('w1', 'r1');

    expect(run.isPaused, isTrue);
    expect(run.currentStep, 'gate');
    expect(run.stepRuns, hasLength(2));
    expect(run.stepRuns.last.status, 'pending_approval');
  });

  test('approveRun posts the decision', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(jsonEncode(runJson(status: 'succeeded')), 200);
    });

    final run = await client(mock).approveRun('w1', 'r1', true);

    expect(captured.url.path, '/api/v1/workflows/w1/runs/r1/approve');
    expect(jsonDecode(captured.body), {'approved': true});
    expect(run.isFinished, isTrue);
  });
}
