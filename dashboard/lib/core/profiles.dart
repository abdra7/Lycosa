import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// One saved controller connection (ADR-006: multiple profiles supported).
class ControllerProfile {
  ControllerProfile({
    required this.id,
    required this.name,
    required this.baseUrl,
    this.token,
  });

  final String id;
  final String name;
  final String baseUrl;
  final String? token; // null = not logged in

  ControllerProfile copyWith({String? name, String? baseUrl, String? Function()? token}) =>
      ControllerProfile(
        id: id,
        name: name ?? this.name,
        baseUrl: baseUrl ?? this.baseUrl,
        token: token != null ? token() : this.token,
      );

  Map<String, dynamic> toJson() =>
      {'id': id, 'name': name, 'baseUrl': baseUrl, 'token': token};

  factory ControllerProfile.fromJson(Map<String, dynamic> json) => ControllerProfile(
        id: json['id'] as String,
        name: json['name'] as String,
        baseUrl: json['baseUrl'] as String,
        token: json['token'] as String?,
      );
}

/// Persistence seam so tests can use an in-memory store.
abstract class ProfileStore {
  Future<List<ControllerProfile>> loadProfiles();
  Future<void> saveProfiles(List<ControllerProfile> profiles);
  Future<String?> loadActiveId();
  Future<void> saveActiveId(String? id);
}

/// Real store: OS keychain via flutter_secure_storage
/// (Windows Credential Manager / macOS Keychain / libsecret).
class SecureProfileStore implements ProfileStore {
  SecureProfileStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _profilesKey = 'lycosa_profiles';
  static const _activeKey = 'lycosa_active_profile';

  final FlutterSecureStorage _storage;

  @override
  Future<List<ControllerProfile>> loadProfiles() async {
    final raw = await _storage.read(key: _profilesKey);
    if (raw == null || raw.isEmpty) return [];
    final list = jsonDecode(raw) as List;
    return list
        .map((e) => ControllerProfile.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  @override
  Future<void> saveProfiles(List<ControllerProfile> profiles) => _storage.write(
        key: _profilesKey,
        value: jsonEncode(profiles.map((p) => p.toJson()).toList()),
      );

  @override
  Future<String?> loadActiveId() => _storage.read(key: _activeKey);

  @override
  Future<void> saveActiveId(String? id) async {
    if (id == null) {
      await _storage.delete(key: _activeKey);
    } else {
      await _storage.write(key: _activeKey, value: id);
    }
  }
}

/// Test / preview store.
class InMemoryProfileStore implements ProfileStore {
  List<ControllerProfile> profiles = [];
  String? activeId;

  @override
  Future<List<ControllerProfile>> loadProfiles() async => List.of(profiles);

  @override
  Future<void> saveProfiles(List<ControllerProfile> value) async =>
      profiles = List.of(value);

  @override
  Future<String?> loadActiveId() async => activeId;

  @override
  Future<void> saveActiveId(String? id) async => activeId = id;
}
