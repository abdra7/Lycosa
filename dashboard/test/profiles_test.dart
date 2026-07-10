import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/core/profiles.dart';

void main() {
  test('profile JSON roundtrip preserves everything including token', () {
    final profile = ControllerProfile(
      id: '1',
      name: 'lab',
      baseUrl: 'http://x:8000',
      token: 'tok',
    );
    final restored = ControllerProfile.fromJson(profile.toJson());
    expect(restored.id, '1');
    expect(restored.name, 'lab');
    expect(restored.baseUrl, 'http://x:8000');
    expect(restored.token, 'tok');
  });

  test('copyWith token setter can clear the token', () {
    final profile = ControllerProfile(
      id: '1',
      name: 'lab',
      baseUrl: 'http://x:8000',
      token: 'tok',
    );
    expect(profile.copyWith(token: () => null).token, isNull);
    expect(profile.copyWith().token, 'tok'); // untouched without setter
  });

  test('in-memory store persists profiles and active id', () async {
    final store = InMemoryProfileStore();
    await store.saveProfiles([
      ControllerProfile(id: 'a', name: 'n', baseUrl: 'http://x'),
    ]);
    await store.saveActiveId('a');

    expect((await store.loadProfiles()).single.id, 'a');
    expect(await store.loadActiveId(), 'a');
  });
}
