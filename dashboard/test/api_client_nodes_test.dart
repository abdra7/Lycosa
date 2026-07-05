import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';

const base = 'http://controller:8000';

Map<String, dynamic> nodeJson({String? role}) => {
      'id': '22222222-2222-2222-2222-222222222222',
      'name': 'gpu-box',
      'status': 'online',
      'role': role,
      'recommended_role': 'hybrid',
      'recommendation_confidence': 0.85,
      'recommendation_rationale': ['Capable GPU can serve models'],
      'cpu_cores': 24,
      'ram_gb': 64.0,
      'gpu_count': 1,
      'gpu_vram_gb': 24.0,
      'storage_gb': 1000.0,
      'os_name': 'Windows 11',
      'hardware_profile': {
        'cpu_model': 'i9-13900K',
        'runtimes': [
          {'name': 'ollama', 'models': ['llama3:8b']}
        ],
        'gpus': [
          {'model': 'RTX 4090', 'vram_gb': 24.0}
        ],
      },
      'last_heartbeat_at': '2026-07-05T10:00:00Z',
      'metrics': {'cpu_percent': 12.5, 'ram_percent': 40.0, 'running_tasks': 0},
      'agent_url': 'http://192.168.1.50:8010',
      'created_at': '2026-07-05T09:00:00Z',
      'updated_at': '2026-07-05T10:00:00Z',
    };

void main() {
  test('listNodes parses the inventory', () async {
    final mock = MockClient(
        (_) async => http.Response(jsonEncode([nodeJson()]), 200));
    final nodes =
        await ApiClient(baseUrl: base, token: 't', httpClient: mock).listNodes();

    expect(nodes, hasLength(1));
    final node = nodes.single;
    expect(node.name, 'gpu-box');
    expect(node.status, 'online');
    expect(node.recommendedRole, 'hybrid');
    expect(node.recommendationConfidence, 0.85);
    expect(node.lastHeartbeatAt, DateTime.utc(2026, 7, 5, 10));
    expect(node.metrics?['cpu_percent'], 12.5);
  });

  test('patchNode sends only the provided fields', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(jsonEncode(nodeJson(role: 'ai_compute')), 200);
    });

    final node = await ApiClient(baseUrl: base, token: 't', httpClient: mock)
        .patchNode('n1', role: 'ai_compute');

    expect(captured.method, 'PATCH');
    expect(jsonDecode(captured.body), {'role': 'ai_compute'});
    expect(node.role, 'ai_compute');
  });

  test('createNodeApiKey returns the one-time key', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(
          jsonEncode({
            'id': 'k1',
            'name': 'garage-box',
            'key_prefix': 'abcd1234',
            'node_id': null,
            'expires_at': null,
            'revoked_at': null,
            'last_used_at': null,
            'created_at': '2026-07-05T10:00:00Z',
            'api_key': 'lyc_abcd1234_secret',
            'role': 'node',
          }),
          201);
    });

    final minted = await ApiClient(baseUrl: base, token: 't', httpClient: mock)
        .createNodeApiKey('garage-box');

    expect(jsonDecode(captured.body), {'name': 'garage-box', 'role': 'node'});
    expect(minted.apiKey, 'lyc_abcd1234_secret');
  });
}
