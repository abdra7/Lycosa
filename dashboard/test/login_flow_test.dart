import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';
import 'package:lycosa_dashboard/main.dart';

/// Fake controller: healthz + login + me + logout, tracking revocation.
MockClient fakeController({String password = 'change-me'}) {
  var revoked = false;
  return MockClient((request) async {
    switch (request.url.path) {
      case '/healthz':
        return http.Response(jsonEncode({'status': 'ok'}), 200);
      case '/api/v1/auth/login':
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        if (body['password'] != password) {
          return http.Response(
            jsonEncode({
              'error': {
                'code': 'unauthorized',
                'message': 'Invalid credentials',
              },
            }),
            401,
          );
        }
        revoked = false;
        return http.Response(
          jsonEncode({
            'access_token': 'tok-abc',
            'token_type': 'bearer',
            'expires_in': 3600,
          }),
          200,
        );
      case '/api/v1/auth/logout':
        revoked = true;
        return http.Response('', 204);
      case '/api/v1/me':
        if (revoked || request.headers['Authorization'] != 'Bearer tok-abc') {
          return http.Response(
            jsonEncode({
              'error': {'code': 'unauthorized', 'message': 'Session revoked'},
            }),
            401,
          );
        }
        return http.Response(
          jsonEncode({
            'type': 'user',
            'id': '11111111-1111-1111-1111-111111111111',
            'role': 'admin',
            'email': 'admin@lycosa.local',
          }),
          200,
        );
      default:
        return http.Response(
          jsonEncode({
            'error': {'code': 'not_found', 'message': 'Not found'},
          }),
          404,
        );
    }
  });
}

Widget appWith(InMemoryProfileStore store, MockClient controller) {
  return ProviderScope(
    overrides: [
      profileStoreProvider.overrideWithValue(store),
      apiClientFactoryProvider.overrideWithValue(
        (baseUrl, {token}) =>
            ApiClient(baseUrl: baseUrl, token: token, httpClient: controller),
      ),
    ],
    child: const LycosaApp(),
  );
}

void main() {
  testWidgets('first run: setup -> connect -> shell shows identity', (
    tester,
  ) async {
    final store = InMemoryProfileStore();
    await tester.pumpWidget(appWith(store, fakeController()));
    await tester.pumpAndSettle();

    expect(find.text('Connect to a controller'), findsOneWidget);

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'admin@lycosa.local',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'change-me',
    );
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();

    // authenticated shell with the /me identity
    expect(find.text('Lycosa'), findsOneWidget);
    expect(find.textContaining('admin@lycosa.local'), findsOneWidget);
    expect(find.text('Nodes'), findsWidgets); // nav rail + selected placeholder

    // profile + token persisted for next launch
    expect(store.profiles.single.token, 'tok-abc');
    expect(store.activeId, store.profiles.single.id);
  });

  testWidgets('bad credentials show the envelope error and stay on setup', (
    tester,
  ) async {
    final store = InMemoryProfileStore();
    await tester.pumpWidget(appWith(store, fakeController()));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'admin@lycosa.local',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'wrong',
    );
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();

    expect(find.text('Invalid credentials'), findsOneWidget);
    expect(store.profiles, isEmpty);
  });

  testWidgets('restored valid token goes straight to the shell', (
    tester,
  ) async {
    final store = InMemoryProfileStore()
      ..profiles = [
        ControllerProfile(
          id: 'p1',
          name: 'lab',
          baseUrl: 'http://c:8000',
          token: 'tok-abc',
        ),
      ]
      ..activeId = 'p1';

    await tester.pumpWidget(appWith(store, fakeController()));
    await tester.pumpAndSettle();

    expect(find.textContaining('admin@lycosa.local'), findsOneWidget);
  });

  testWidgets('revoked token drops to the login screen', (tester) async {
    final controller = fakeController();
    final store = InMemoryProfileStore()
      ..profiles = [
        ControllerProfile(
          id: 'p1',
          name: 'lab',
          baseUrl: 'http://c:8000',
          token: 'tok-stale',
        ),
      ]
      ..activeId = 'p1';

    await tester.pumpWidget(appWith(store, controller));
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsWidgets);
    expect(store.profiles.single.token, isNull); // stale token wiped
  });

  testWidgets('logout revokes and returns to login', (tester) async {
    final store = InMemoryProfileStore()
      ..profiles = [
        ControllerProfile(
          id: 'p1',
          name: 'lab',
          baseUrl: 'http://c:8000',
          token: 'tok-abc',
        ),
      ]
      ..activeId = 'p1';

    await tester.pumpWidget(appWith(store, fakeController()));
    await tester.pumpAndSettle();
    expect(find.textContaining('admin@lycosa.local'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.logout));
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsWidgets);
    expect(store.profiles.single.token, isNull);
  });
}
