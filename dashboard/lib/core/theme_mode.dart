import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persistence seam so tests can use an in-memory store.
abstract class ThemeModeStore {
  Future<String?> load();
  Future<void> save(String value);
}

class SecureThemeModeStore implements ThemeModeStore {
  SecureThemeModeStore([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'lycosa_theme_mode';

  final FlutterSecureStorage _storage;

  @override
  Future<String?> load() => _storage.read(key: _key);

  @override
  Future<void> save(String value) => _storage.write(key: _key, value: value);
}

class InMemoryThemeModeStore implements ThemeModeStore {
  String? value;

  @override
  Future<String?> load() async => value;

  @override
  Future<void> save(String v) async => value = v;
}

final themeModeStoreProvider = Provider<ThemeModeStore>(
  (ref) => SecureThemeModeStore(),
);

/// Saved theme read before `runApp` so the very first frame is already the
/// right theme — no light flash for dark-mode users. Light on empty/error.
Future<ThemeMode> loadInitialThemeMode(ThemeModeStore store) async {
  try {
    return await store.load() == 'dark' ? ThemeMode.dark : ThemeMode.light;
  } on Exception {
    return ThemeMode.light;
  }
}

/// Seeded in main() with the pre-frame value; light-first per the brand spec.
final initialThemeModeProvider = Provider<ThemeMode>((_) => ThemeMode.light);

/// Light/dark preference. Starts from the pre-frame initial value and
/// persists every change.
class ThemeModeController extends Notifier<ThemeMode> {
  @override
  ThemeMode build() => ref.read(initialThemeModeProvider);

  void toggle() =>
      set(state == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark);

  void set(ThemeMode mode) {
    state = mode;
    // fire-and-forget: the UI shouldn't wait on the keychain
    ref
        .read(themeModeStoreProvider)
        .save(mode == ThemeMode.dark ? 'dark' : 'light')
        .ignore();
  }
}

final themeModeProvider = NotifierProvider<ThemeModeController, ThemeMode>(
  ThemeModeController.new,
);
