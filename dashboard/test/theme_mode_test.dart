import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/core/theme_mode.dart';

class ThrowingThemeModeStore implements ThemeModeStore {
  @override
  Future<String?> load() async => throw Exception('keychain unavailable');

  @override
  Future<void> save(String value) async =>
      throw Exception('keychain unavailable');
}

void main() {
  group('loadInitialThemeMode (pre-frame read, no flash)', () {
    test('empty store defaults to light', () async {
      expect(
        await loadInitialThemeMode(InMemoryThemeModeStore()),
        ThemeMode.light,
      );
    });

    test('restores saved dark', () async {
      final store = InMemoryThemeModeStore()..value = 'dark';
      expect(await loadInitialThemeMode(store), ThemeMode.dark);
    });

    test('restores saved light', () async {
      final store = InMemoryThemeModeStore()..value = 'light';
      expect(await loadInitialThemeMode(store), ThemeMode.light);
    });

    test('storage failure falls back to light', () async {
      expect(
        await loadInitialThemeMode(ThrowingThemeModeStore()),
        ThemeMode.light,
      );
    });
  });

  group('ThemeModeController', () {
    test('first read already matches the seeded initial value', () {
      final container = ProviderContainer(
        overrides: [initialThemeModeProvider.overrideWithValue(ThemeMode.dark)],
      );
      addTearDown(container.dispose);

      // no async restore involved: dark from the very first read
      expect(container.read(themeModeProvider), ThemeMode.dark);
    });

    test('toggle flips the mode and persists it', () async {
      final store = InMemoryThemeModeStore();
      final container = ProviderContainer(
        overrides: [themeModeStoreProvider.overrideWithValue(store)],
      );
      addTearDown(container.dispose);

      expect(container.read(themeModeProvider), ThemeMode.light);
      container.read(themeModeProvider.notifier).toggle();
      expect(container.read(themeModeProvider), ThemeMode.dark);
      await Future<void>.delayed(Duration.zero); // fire-and-forget save lands
      expect(store.value, 'dark');

      container.read(themeModeProvider.notifier).toggle();
      expect(container.read(themeModeProvider), ThemeMode.light);
      await Future<void>.delayed(Duration.zero);
      expect(store.value, 'light');
    });
  });
}
