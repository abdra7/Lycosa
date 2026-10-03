import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';
import 'openrouter_sign_in.dart';
import 'providers.dart';

/// Connect a provider account: API key (or none, for local runtimes), or
/// OpenRouter's official sign-in. The key is sent once and never shown again.
class ConnectAccountDialog extends ConsumerStatefulWidget {
  const ConnectAccountDialog({
    super.key,
    required this.provider,
    required this.isAdmin,
  });

  final LlmProvider provider;
  final bool isAdmin;

  @override
  ConsumerState<ConnectAccountDialog> createState() =>
      _ConnectAccountDialogState();
}

class _ConnectAccountDialogState extends ConsumerState<ConnectAccountDialog> {
  late final _label = TextEditingController(text: 'Personal');
  late final _baseUrl = TextEditingController(
    text: widget.provider.defaultBaseUrl,
  );
  final _key = TextEditingController();
  final _code = TextEditingController();
  String _scope = 'personal';
  bool _busy = false;
  String? _error;
  OpenRouterSignIn? _signIn;
  LoopbackCodeReceiver? _receiver;

  LlmProvider get _p => widget.provider;

  List<String> get _officialChoices =>
      _p.officialBaseUrls.where((u) => !u.contains('{')).toList();

  @override
  void dispose() {
    _key.clear();
    for (final c in [_label, _baseUrl, _key, _code]) {
      c.dispose();
    }
    _receiver?.close();
    super.dispose();
  }

  Future<void> _connect() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    final key = _key.text.trim();
    if (_p.credentialRequired && key.isEmpty) {
      setState(() => _error = '${_p.displayName} needs an API key.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await client.connectLlmAccount(
        provider: _p.id,
        label: _label.text.trim(),
        scope: _scope,
        baseUrl: _p.takesBaseUrl ? _baseUrl.text.trim() : null,
        apiKey: key.isEmpty ? null : key,
      );
      _key.clear();
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on StateError catch (e) {
      setState(() => _error = e.message);
    } on ControllerUnreachableException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// OpenRouter PKCE: the controller keeps the verifier; the browser returns
  /// the code to a loopback listener (or the user pastes it).
  Future<void> _startSignIn() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final receiver = LoopbackCodeReceiver();
    try {
      final callback = await receiver.start();
      final signIn = await client.startOpenRouterSignIn(callbackUrl: callback);
      _receiver = receiver;
      setState(() => _signIn = signIn);
      final opened = await ref.read(browserLauncherProvider)(
        signIn.authorizationUrl,
      );
      if (!opened && mounted) {
        setState(() => _error = 'Open the sign-in link below in your browser.');
      }
      final code = await receiver.waitForCode();
      if (code != null && mounted) await _complete(code);
    } on ApiException catch (e) {
      await receiver.close();
      if (mounted) setState(() => _error = e.friendly);
    } on Exception {
      await receiver.close();
      if (mounted) {
        setState(() => _error = 'Sign-in could not start on this device.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _complete(String code) async {
    final client = ref.read(activeApiClientProvider);
    final signIn = _signIn;
    if (client == null || signIn == null) return;
    setState(() => _busy = true);
    try {
      await client.completeOpenRouterSignIn(
        flow: signIn.flow,
        code: code,
        label: _label.text.trim(),
        scope: _scope,
      );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on StateError catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodySmall;
    return AlertDialog(
      title: Text('Connect ${_p.displayName}'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_p.subscriptionNote != null) ...[
                Text(_p.subscriptionNote!),
                const SizedBox(height: 12),
              ],
              for (final note in _p.notes) ...[
                Text(note, style: muted),
                const SizedBox(height: 8),
              ],
              TextField(
                controller: _label,
                decoration: const InputDecoration(
                  labelText: 'Account label',
                  hintText: 'Personal, Work…',
                ),
                enabled: !_busy,
              ),
              if (widget.isAdmin) ...[
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: _scope,
                  decoration: const InputDecoration(
                    labelText: 'Who can use it',
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: 'personal',
                      child: Text('Only me (personal)'),
                    ),
                    DropdownMenuItem(
                      value: 'deployment',
                      child: Text('Every operator (shared deployment account)'),
                    ),
                  ],
                  onChanged: _busy ? null : (v) => setState(() => _scope = v!),
                ),
              ],
              if (_p.takesBaseUrl) ...[
                const SizedBox(height: 12),
                if (_p.baseUrlMode == 'official_choice' &&
                    _officialChoices.isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue: _officialChoices.contains(_baseUrl.text)
                        ? _baseUrl.text
                        : _officialChoices.first,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Official endpoint (region)',
                    ),
                    items: [
                      for (final url in _officialChoices)
                        DropdownMenuItem(value: url, child: Text(url)),
                    ],
                    onChanged: _busy ? null : (v) => _baseUrl.text = v!,
                  )
                else
                  TextField(
                    controller: _baseUrl,
                    decoration: InputDecoration(
                      labelText: 'Endpoint URL',
                      helperText: _p.isLocal
                          ? 'The controller connects to this address; it must be '
                                'on a network your administrator allowed.'
                          : null,
                    ),
                    enabled: !_busy,
                  ),
              ],
              const SizedBox(height: 12),
              TextField(
                controller: _key,
                obscureText: true,
                enableSuggestions: false,
                autocorrect: false,
                decoration: InputDecoration(
                  labelText: _p.credentialRequired
                      ? 'API key'
                      : 'API key / token (optional)',
                  helperText: 'Encrypted by the controller; never shown again.',
                ),
                enabled: !_busy,
              ),
              if (_p.supportsOAuth) ...[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  icon: const Icon(Icons.login),
                  label: const Text('Sign in with OpenRouter instead'),
                  onPressed: _busy ? null : _startSignIn,
                ),
                if (_signIn != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    'Waiting for OpenRouter… If the browser did not open, '
                    'copy the link. If no code arrives, paste it below.',
                    style: muted,
                  ),
                  Row(
                    children: [
                      Expanded(
                        child: SelectableText(
                          _signIn!.authorizationUrl,
                          maxLines: 2,
                          style: muted,
                        ),
                      ),
                      IconButton(
                        tooltip: 'Copy link',
                        icon: const Icon(Icons.copy, size: 16),
                        onPressed: () => Clipboard.setData(
                          ClipboardData(text: _signIn!.authorizationUrl),
                        ),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _code,
                          decoration: const InputDecoration(
                            labelText: 'Authorization code',
                            isDense: true,
                          ),
                        ),
                      ),
                      TextButton(
                        onPressed: () => _code.text.trim().isEmpty
                            ? null
                            : _complete(_code.text.trim()),
                        child: const Text('Use code'),
                      ),
                    ],
                  ),
                ],
              ],
              if (_busy) ...[
                const SizedBox(height: 12),
                const LinearProgressIndicator(),
              ],
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _busy ? null : _connect,
          child: const Text('Connect'),
        ),
      ],
    );
  }
}
