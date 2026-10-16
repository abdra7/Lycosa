import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/features/workflows/workflows_screen.dart';

import 'tasks_workflows_screens_test.dart' show appWith, settle;

Map<String, dynamic> workflowJson(String id, String name) => {
  'id': id,
  'name': name,
  'description': 'plan, review, do',
  'definition': {
    'steps': [
      {'id': 'plan', 'kind': 'task', 'prompt': 'Plan how to: {{input}}'},
      {'id': 'gate', 'kind': 'approval', 'message': 'Review the plan'},
    ],
  },
  'created_at': '2026-10-16T10:00:00Z',
};

/// A controller whose workflow list shrinks when a DELETE lands.
MockClient workflowsController(
  List<Map<String, dynamic>> workflows, {
  List<http.Request>? captured,
  int deleteStatus = 204,
}) {
  final one = RegExp(r'^/api/v1/workflows/([^/]+)$');
  return MockClient((request) async {
    captured?.add(request);
    final path = request.url.path;
    if (path == '/api/v1/me') {
      return http.Response(
        jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
        200,
      );
    }
    if (path == '/api/v1/workflows' && request.method == 'GET') {
      return http.Response(jsonEncode(workflows), 200);
    }
    final match = one.firstMatch(path);
    if (match != null && request.method == 'DELETE') {
      if (deleteStatus != 204) {
        return http.Response(
          jsonEncode({
            'error': {
              'code': 'conflict',
              'message':
                  "Workflow 'release' has 1 run(s) still running or waiting "
                  'for approval; approve or reject them first',
            },
          }),
          deleteStatus,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }
      workflows.removeWhere((w) => w['id'] == match.group(1));
      return http.Response('', 204);
    }
    return http.Response(
      jsonEncode({
        'error': {'code': 'not_found', 'message': 'nope'},
      }),
      404,
    );
  });
}

Future<void> pumpWorkflows(WidgetTester tester, MockClient controller) async {
  await tester.pumpWidget(
    appWith(controller, home: const Scaffold(body: WorkflowsScreen())),
  );
  await settle(tester);
}

Finder rowOf(String name) =>
    find.ancestor(of: find.text(name), matching: find.byType(ListTile));

void main() {
  // Sprint 14 computer-use walkthrough: the list only offered Run
  testWidgets('each workflow offers View, Run and Delete', (tester) async {
    await pumpWorkflows(
      tester,
      workflowsController([
        workflowJson('w1', 'release'),
        workflowJson('w2', 'triage'),
      ]),
    );
    for (final name in ['release', 'triage']) {
      final row = rowOf(name);
      expect(find.descendant(of: row, matching: find.text('View')), findsOne);
      expect(find.descendant(of: row, matching: find.text('Run')), findsOne);
      expect(
        find.descendant(of: row, matching: find.byTooltip('Delete workflow')),
        findsOne,
      );
    }
  });

  testWidgets('View shows the definition read-only', (tester) async {
    await pumpWorkflows(
      tester,
      workflowsController([workflowJson('w1', 'release')]),
    );
    await tester.tap(find.text('View'));
    await settle(tester);

    final dialog = find.byType(AlertDialog);
    expect(
      find.descendant(of: dialog, matching: find.text('release')),
      findsOne,
    );
    expect(
      find.descendant(
        of: dialog,
        matching: find.textContaining('Plan how to: {{input}}'),
      ),
      findsWidgets,
    );
    expect(
      find.descendant(of: dialog, matching: find.textContaining('2 steps')),
      findsOne,
    );
    // nothing in the viewer can be edited
    expect(
      find.descendant(of: dialog, matching: find.byType(TextField)),
      findsNothing,
    );
    await tester.tap(find.text('Close'));
    await settle(tester);
    expect(find.byType(AlertDialog), findsNothing);
  });

  testWidgets('Delete asks first, then removes the workflow', (tester) async {
    final captured = <http.Request>[];
    await pumpWorkflows(
      tester,
      workflowsController([
        workflowJson('w1', 'release'),
        workflowJson('w2', 'triage'),
      ], captured: captured),
    );
    bool deleted() => captured.any(
      (r) => r.method == 'DELETE' && r.url.path == '/api/v1/workflows/w1',
    );

    await tester.tap(
      find.descendant(
        of: rowOf('release'),
        matching: find.byTooltip('Delete workflow'),
      ),
    );
    await settle(tester);
    expect(find.text('Delete workflow?'), findsOne);
    expect(find.textContaining('"release"'), findsOne);
    expect(find.textContaining('run history'), findsOne);
    await tester.tap(find.text('Cancel'));
    await settle(tester);
    expect(deleted(), isFalse);

    await tester.tap(
      find.descendant(
        of: rowOf('release'),
        matching: find.byTooltip('Delete workflow'),
      ),
    );
    await settle(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await settle(tester);
    expect(deleted(), isTrue);
    expect(find.text('release'), findsNothing);
    expect(find.text('triage'), findsOne);
  });

  testWidgets('a refused delete explains why and keeps the workflow', (
    tester,
  ) async {
    await pumpWorkflows(
      tester,
      workflowsController([workflowJson('w1', 'release')], deleteStatus: 409),
    );
    await tester.tap(find.byTooltip('Delete workflow'));
    await settle(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await settle(tester);
    expect(find.textContaining('approve or reject them first'), findsOne);
    expect(find.text('release'), findsOne);
  });
}
