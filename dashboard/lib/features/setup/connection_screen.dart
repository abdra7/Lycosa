import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_exception.dart';
import '../../core/session.dart';
import '../../widgets/lycosa_brand.dart';

/// First-run (and add-profile) flow: controller URL + credentials.
class ConnectionScreen extends ConsumerStatefulWidget {
  const ConnectionScreen({super.key});

  @override
  ConsumerState<ConnectionScreen> createState() => _ConnectionScreenState();
}

class _ConnectionScreenState extends ConsumerState<ConnectionScreen> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _url = TextEditingController(text: 'http://localhost:8000');
  final _email = TextEditingController();
  final _password = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    for (final c in [_name, _url, _email, _password]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _connect() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(sessionProvider.notifier)
          .addProfileAndLogin(
            name: _name.text.trim(),
            baseUrl: _url.text.trim(),
            email: _email.text.trim(),
            password: _password.text,
          );
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on ControllerUnreachableException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLow,
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const LycosaBrandHero(
                subtitle: 'AI operations for your local fabric',
              ),
              const SizedBox(height: 20),
              Card(
                margin: const EdgeInsets.symmetric(horizontal: 24),
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          'Connect to a controller',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 16),
                        TextFormField(
                          controller: _url,
                          decoration: const InputDecoration(
                            labelText: 'Controller URL',
                            hintText: 'http://controller-host:8000',
                          ),
                          validator: (v) {
                            final uri = Uri.tryParse(v?.trim() ?? '');
                            if (uri == null ||
                                !uri.hasScheme ||
                                uri.host.isEmpty) {
                              return 'Enter a valid URL like http://host:8000';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: 8),
                        TextFormField(
                          controller: _name,
                          decoration: const InputDecoration(
                            labelText: 'Profile name (optional)',
                            hintText: 'Home lab',
                          ),
                        ),
                        const SizedBox(height: 8),
                        TextFormField(
                          controller: _email,
                          decoration: const InputDecoration(labelText: 'Email'),
                          validator: (v) => (v == null || v.trim().isEmpty)
                              ? 'Email is required'
                              : null,
                        ),
                        const SizedBox(height: 8),
                        TextFormField(
                          controller: _password,
                          obscureText: true,
                          decoration: const InputDecoration(
                            labelText: 'Password',
                          ),
                          validator: (v) => (v == null || v.isEmpty)
                              ? 'Password is required'
                              : null,
                          onFieldSubmitted: (_) => _connect(),
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
                          onPressed: _busy ? null : _connect,
                          child: _busy
                              ? const SizedBox(
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Text('Connect'),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
