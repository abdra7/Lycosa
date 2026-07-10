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

/// Light/dark preference. Defaults to light (brand spec is light-first),
/// restores the saved choice on startup, and persists every change.
class ThemeModeController extends Notifier<ThemeMode> {
  @override
  ThemeMode build() {
    _restore();
    return ThemeMode.light;
  }

  Future<void> _restore() async {
    try {
      final saved = await ref.read(themeModeStoreProvider).load();
      if (saved == 'dark') state = ThemeMode.dark;
    } on Exception {
      // storage unavailable (e.g. tests) → keep the light default
    }
  }

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
