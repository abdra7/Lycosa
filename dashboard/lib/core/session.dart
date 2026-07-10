import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import 'api_client.dart';
import 'api_exception.dart';
import 'profiles.dart';

/// Seams tests override.
final profileStoreProvider = Provider<ProfileStore>(
  (ref) => SecureProfileStore(),
);

typedef ApiClientFactory = ApiClient Function(String baseUrl, {String? token});

final apiClientFactoryProvider = Provider<ApiClientFactory>(
  (ref) =>
      (baseUrl, {token}) =>
          ApiClient(baseUrl: baseUrl, token: token, httpClient: http.Client()),
);

class SessionState {
  const SessionState({this.profiles = const [], this.activeId, this.principal});

  final List<ControllerProfile> profiles;
  final String? activeId;
  final Principal? principal; // non-null = authenticated on the active profile

  ControllerProfile? get activeProfile {
    for (final p in profiles) {
      if (p.id == activeId) return p;
    }
    return profiles.isEmpty ? null : profiles.first;
  }

  bool get needsSetup => profiles.isEmpty;
  bool get authenticated => principal != null;

  SessionState copyWith({
    List<ControllerProfile>? profiles,
    String? Function()? activeId,
    Principal? Function()? principal,
  }) => SessionState(
    profiles: profiles ?? this.profiles,
    activeId: activeId != null ? activeId() : this.activeId,
    principal: principal != null ? principal() : this.principal,
  );
}

class SessionController extends AsyncNotifier<SessionState> {
  ProfileStore get _store => ref.read(profileStoreProvider);
  ApiClientFactory get _client => ref.read(apiClientFactoryProvider);

  @override
  Future<SessionState> build() async {
    final profiles = await _store.loadProfiles();
    final activeId = await _store.loadActiveId();
    var state = SessionState(profiles: profiles, activeId: activeId);

    // restore: validate the stored token against /me; a revoked/expired
    // token silently drops back to the login screen
    final active = state.activeProfile;
    if (active?.token != null) {
      try {
        final principal = await _client(
          active!.baseUrl,
          token: active.token,
        ).me();
        state = state.copyWith(principal: () => principal);
      } on ApiException catch (e) {
        if (e.isUnauthorized) {
          state = await _updateProfile(
            state,
            active!.copyWith(token: () => null),
          );
        } else {
          rethrow;
        }
      } on ControllerUnreachableException {
        // controller down: keep the token, show login-ish state; user can retry
      }
    }
    return state;
  }

  Future<SessionState> _updateProfile(
    SessionState state,
    ControllerProfile updated,
  ) async {
    final profiles = state.profiles
        .map((p) => p.id == updated.id ? updated : p)
        .toList();
    await _store.saveProfiles(profiles);
    return state.copyWith(profiles: profiles);
  }

  /// Connection-setup flow: probe the controller, then log in, then persist.
  Future<void> addProfileAndLogin({
    required String name,
    required String baseUrl,
    required String email,
    required String password,
  }) async {
    final probe = _client(baseUrl);
    await probe.healthz();
    final token = await probe.login(email, password);
    final principal = await _client(baseUrl, token: token).me();

    final current = state.value ?? const SessionState();
    final profile = ControllerProfile(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
      name: name.isEmpty ? Uri.parse(baseUrl).host : name,
      baseUrl: baseUrl,
      token: token,
    );
    final profiles = [...current.profiles, profile];
    await _store.saveProfiles(profiles);
    await _store.saveActiveId(profile.id);
    state = AsyncData(
      SessionState(
        profiles: profiles,
        activeId: profile.id,
        principal: principal,
      ),
    );
  }

  /// Login on the already-active profile.
  Future<void> login(String email, String password) async {
    final current = state.value!;
    final profile = current.activeProfile!;
    final token = await _client(profile.baseUrl).login(email, password);
    final principal = await _client(profile.baseUrl, token: token).me();
    final updated = await _updateProfile(
      current,
      profile.copyWith(token: () => token),
    );
    state = AsyncData(updated.copyWith(principal: () => principal));
  }

  Future<void> logout() async {
    final current = state.value!;
    final profile = current.activeProfile;
    if (profile?.token != null) {
      try {
        await _client(profile!.baseUrl, token: profile.token).logout();
      } on Exception {
        // token wiped locally regardless; server session expires on its own
      }
      final updated = await _updateProfile(
        current,
        profile!.copyWith(token: () => null),
      );
      state = AsyncData(updated.copyWith(principal: () => null));
    }
  }

  Future<void> switchProfile(String id) async {
    await _store.saveActiveId(id);
    ref.invalidateSelf(); // rebuild restores the target profile's session
  }
}

final sessionProvider = AsyncNotifierProvider<SessionController, SessionState>(
  SessionController.new,
);

/// Client bound to the active authenticated profile — screens in 8b+ use this.
final activeApiClientProvider = Provider<ApiClient?>((ref) {
  final session = ref.watch(sessionProvider).value;
  final profile = session?.activeProfile;
  if (profile?.token == null) return null;
  return ref.read(apiClientFactoryProvider)(
    profile!.baseUrl,
    token: profile.token,
  );
});
