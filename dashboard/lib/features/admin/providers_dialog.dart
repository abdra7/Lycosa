import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/session.dart';
import '../../core/provider_catalog.dart';

class ProvidersDialog extends ConsumerStatefulWidget {
  const ProvidersDialog({super.key});
  @override
  ConsumerState<ProvidersDialog> createState() => _ProvidersDialogState();
}

class _ProvidersDialogState extends ConsumerState<ProvidersDialog> {
  final _key = TextEditingController();
  String _provider = 'openrouter';
  String _storage = 'session';
  String _message = '';
  bool _busy = false;
  List<String> _providers = providerLabels.keys.where((p) => p != 'ollama').toList();

  @override
  void initState() {
    super.initState();
    _loadProviders();
  }

  Future<void> _loadProviders() async {
    try {
      final items = await ref.read(activeApiClientProvider)?.providerCatalog();
      if (mounted && items != null && items.isNotEmpty) {
        setState(() => _providers = items
            .where((p) => p['execution_mode'] == 'cloud')
            .map((p) => p['name'] as String)
            .toList());
      }
    } catch (_) {
      // Preserve the default catalogue on a disconnected controller.
    }
  }

  @override
  void dispose() {
    _key.clear();
    _key.dispose();
    super.dispose();
  }

  Future<void> _action(String action) async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() {
      _busy = true;
      _message = '';
    });
    try {
      if (action == 'save') {
        await client.saveProviderKey(_provider, _key.text, _storage);
        _key.clear();
      } else if (action == 'delete') {
        await client.deleteProviderKey(_provider, _storage);
      }
      final providers = await client.listProviders();
      final selected = providers.firstWhere((p) => p['name'] == _provider);
      if (mounted) {
        setState(
          () => _message = selected['credential_configured'] == true
              ? 'Key configured (not yet validated). Use Tasks to test the model.'
              : 'No key configured.',
        );
      }
    } catch (_) {
      // Never render exceptions from a credential-bearing request.
      if (mounted) {
        setState(
          () => _message =
              'Operation failed. Check controller access, HTTPS/loopback URL, and storage support. Session mode requires one worker; vault mode requires a controller OS vault.',
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Providers'),
    content: SizedBox(
      width: 560,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Cloud providers require administrator-enabled model policies and credentials. New adapters run on the controller; Anthropic retains its trusted-agent route. Bedrock and Vertex AI may instead use configured workload identity. Phantom never uses cloud providers.',
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: _provider,
              decoration: const InputDecoration(labelText: 'Provider'),
              items: [
                for (final p in _providers)
                  DropdownMenuItem(value: p, child: Text(providerLabels[p] ?? p)),
              ],
              onChanged: _busy
                  ? null
                  : (v) => setState(() {
                      _provider = v!;
                      _storage = 'session';
                      _key.clear();
                      _message = '';
                    }),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: ValueKey(_provider),
              initialValue: _storage,
              decoration: const InputDecoration(labelText: 'Storage'),
              items: const [
                DropdownMenuItem(
                  value: 'session',
                  child: Text('Controller memory (until restart)'),
                ),
                DropdownMenuItem(
                  value: 'vault',
                  child: Text('Controller OS credential vault'),
                ),
              ],
              onChanged: _busy ? null : (v) => setState(() => _storage = v!),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _key,
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'API key'),
              enabled: !_busy,
            ),
            const SizedBox(height: 12),
            const Text(
              'Saving authorizes permitted controller users to use this provider. Session storage is temporary and single-worker only. Removing a session key can reveal an existing vault key; Check status shows effective availability.',
            ),
            const SizedBox(height: 12),
            const Text(
              'Chat subscription login is not implemented here. API keys are separate from Claude Pro / ChatGPT Plus sign-in; no subscription access is claimed.',
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_message.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_message),
              ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: _busy ? null : () => _action('status'),
        child: const Text('Check status'),
      ),
      TextButton(
        onPressed: _busy ? null : () => _action('delete'),
        child: const Text('Remove selected key'),
      ),
      FilledButton(
        onPressed: _busy ? null : () => _action('save'),
        child: const Text('Save key'),
      ),
      TextButton(
        onPressed: _busy ? null : () => Navigator.of(context).pop(),
        child: const Text('Close'),
      ),
    ],
  );
}
