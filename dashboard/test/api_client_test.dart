import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/api_exception.dart';

const base = 'http://controller:8000';

ApiClient clientWith(MockClient mock, {String? token}) =>
    ApiClient(baseUrl: base, token: token, httpClient: mock);

void main() {
  test('login returns the access token and sends credentials', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(
        jsonEncode({
          'access_token': 'tok123',
          'token_type': 'bearer',
          'expires_in': 60,
        }),
        200,
      );
    });

    final token = await clientWith(mock).login('a@b.c', 'pw');

    expect(token, 'tok123');
    expect(captured.url.path, '/api/v1/auth/login');
    expect(jsonDecode(captured.body), {'email': 'a@b.c', 'password': 'pw'});
  });

  test('envelope errors become ApiException with code and message', () async {
    final mock = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'error': {'code': 'unauthorized', 'message': 'Invalid credentials'},
        }),
        401,
      ),
    );

    expect(
      () => clientWith(mock).login('a@b.c', 'wrong'),
      throwsA(
        isA<ApiException>()
            .having((e) => e.code, 'code', 'unauthorized')
            .having((e) => e.isUnauthorized, 'isUnauthorized', true)
            .having((e) => e.friendly, 'friendly', 'Invalid credentials'),
      ),
    );
  });

  test('validation details are folded into the friendly message', () async {
    final mock = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'error': {
            'code': 'validation_error',
            'message': 'Request validation failed',
            'details': [
              {'field': 'body.password', 'message': 'Field required'},
            ],
          },
        }),
        422,
      ),
    );

    try {
      await clientWith(mock).login('a@b.c', '');
      fail('expected ApiException');
    } on ApiException catch (e) {
      expect(e.friendly, contains('body.password: Field required'));
    }
  });

  test('me sends the bearer token and parses the principal', () async {
    late http.Request captured;
    final mock = MockClient((request) async {
      captured = request;
      return http.Response(
        jsonEncode({
          'type': 'user',
          'id': '11111111-1111-1111-1111-111111111111',
          'role': 'admin',
          'email': 'admin@lycosa.local',
          'name': null,
        }),
        200,
      );
    });

    final principal = await clientWith(mock, token: 'tok123').me();

    expect(captured.headers['Authorization'], 'Bearer tok123');
    expect(principal.role, 'admin');
    expect(principal.displayName, 'admin@lycosa.local');
  });

  test(
    'unreachable controller raises ControllerUnreachableException',
    () async {
      final mock = MockClient(
        (_) async => throw http.ClientException('refused'),
      );

      expect(
        () => clientWith(mock).healthz(),
        throwsA(isA<ControllerUnreachableException>()),
      );
    },
  );
}
