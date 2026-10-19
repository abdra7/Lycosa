import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_exception.dart';
import '../../core/profiles.dart';
import '../../core/session.dart';
import '../../widgets/lycosa_brand.dart';
import '../setup/connection_screen.dart';

/// Login for an existing profile (token missing, expired, or revoked).
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(sessionProvider.notifier)
          .login(_email.text.trim(), _password.text);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _error = e.friendly);
      }
    } on ControllerUnreachableException catch (e) {
      if (mounted) {
        setState(() => _error = e.friendly);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _addController() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => const ConnectionScreen(adding: true),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider).value;
    final profile = session?.activeProfile;
    final profiles = session?.profiles ?? const <ControllerProfile>[];
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLow,
      // scrolls when the window is short (small screens, display scaling)
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(vertical: 24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 380),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const LycosaBrandHero(),
                const SizedBox(height: 20),
                Card(
                  margin: const EdgeInsets.symmetric(horizontal: 24),
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          'Sign in',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        if (profile != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    '${profile.name} — ${profile.baseUrl}',
                                    style: Theme.of(
                                      context,
                                    ).textTheme.bodySmall,
                                  ),
                                ),
                                if (profiles.length > 1)
                                  PopupMenuButton<String>(
                                    tooltip: 'Switch controller',
                                    icon: const Icon(
                                      Icons.swap_horiz,
                                      size: 20,
                                    ),
                                    onSelected: (id) => ref
                                        .read(sessionProvider.notifier)
                                        .switchProfile(id),
                                    itemBuilder: (_) => [
                                      for (final p in profiles)
                                        PopupMenuItem(
                                          value: p.id,
                                          child: Text(
                                            '${p.name} — ${p.baseUrl}',
                                          ),
                                        ),
                                    ],
                                  ),
                              ],
                            ),
                          ),
                        const SizedBox(height: 16),
                        TextField(
                          controller: _email,
                          decoration: const InputDecoration(labelText: 'Email'),
                        ),
                        const SizedBox(height: 8),
                        TextField(
                          controller: _password,
                          obscureText: true,
                          decoration: const InputDecoration(
                            labelText: 'Password',
                          ),
                          onSubmitted: (_) => _login(),
                        ),
                        const SizedBox(height: 16),
                        if (_error != null)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: Text(
                              _error!,
                              style: TextStyle(
                                color: Theme.of(context).colorScheme.error,
                              ),
                            ),
                          ),
                        FilledButton(
                          onPressed: _busy ? null : _login,
                          child: _busy
                              ? const SizedBox(
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Text('Sign in'),
                        ),
                        const SizedBox(height: 8),
                        TextButton(
                          onPressed: _busy ? null : _addController,
                          child: const Text('Use a different controller'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
