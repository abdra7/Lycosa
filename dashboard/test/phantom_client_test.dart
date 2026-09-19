import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';

void main() {
  test('Phantom uses isolated endpoint and disables redirect forwarding', () async {
    final calls = <http.Request>[];
    final client = ApiClient(
      baseUrl: 'https://controller.invalid',
      httpClient: MockClient((request) async {
        calls.add(request);
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/phantom/tasks');
        expect(request.followRedirects, isFalse);
        expect(request.headers['Cache-Control'], 'no-store');
        expect(jsonDecode(request.body), {
          'prompt': 'synthetic',
          'model': 'private_model',
          'provider': 'phantom_local',
        });
        return http.Response(jsonEncode({
          'output': 'insight', 'persisted': false, 'cleanup': 'confirmed',
        }), 200);
      }),
    );
    final result = await client.submitPhantom(prompt: 'synthetic', model: 'private_model');
    expect(result['persisted'], isFalse);
    expect(calls, hasLength(1));
  });

  test('Phantom refuses unencrypted remote input before sending', () async {
    final client = ApiClient(
      baseUrl: 'http://remote.invalid',
      httpClient: MockClient((_) async => fail('must not send sensitive input')),
    );
    await expectLater(
      client.submitPhantom(prompt: 'synthetic', model: 'private_model'),
      throwsStateError,
    );
  });

  test('Phantom rejects redirect instead of resubmitting input elsewhere', () async {
    var calls = 0;
    final client = ApiClient(
      baseUrl: 'https://controller.invalid',
      httpClient: MockClient((_) async {
        calls++;
        return http.Response('', 307, headers: {'location': 'https://other.invalid'});
      }),
    );
    await expectLater(
      client.submitPhantom(prompt: 'synthetic', model: 'private_model'),
      throwsStateError,
    );
    expect(calls, 1);
  });
}
