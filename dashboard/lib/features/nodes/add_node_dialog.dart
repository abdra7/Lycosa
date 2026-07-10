import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';

/// Admin flow: mint a node API key and hand the operator the exact
/// command to run on the new machine. The key is shown exactly once.
class AddNodeDialog extends ConsumerStatefulWidget {
  const AddNodeDialog({super.key});

  @override
  ConsumerState<AddNodeDialog> createState() => _AddNodeDialogState();
}

class _AddNodeDialogState extends ConsumerState<AddNodeDialog> {
  final _name = TextEditingController();
  MintedApiKey? _minted;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _mint() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _name.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final minted = await client.createNodeApiKey(_name.text.trim());
      setState(() => _minted = minted);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // The dashboard is a desktop app, so default the shown commands to the OS the
  // operator is on — but agents can target any OS, so both forms are offered.
  bool get _windows => Platform.isWindows;

  static const _installPowershell =
      'irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex';
  static const _installBash =
      'curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash';

  String get _baseUrl =>
      ref.read(sessionProvider).value?.activeProfile?.baseUrl ??
      'http://<controller>:8000';

  // PowerShell: each var on its own line, no line continuations.
  String get _runPowershell =>
      '\$env:LYCOSA_CONTROLLER_URL = "$_baseUrl"\n'
      '\$env:LYCOSA_API_KEY = "${_minted?.apiKey}"\n'
      'lycosa-agent run';

  // bash: env assignments with backslash continuations.
  String get _runBash =>
      'LYCOSA_CONTROLLER_URL=$_baseUrl \\\n'
      'LYCOSA_API_KEY=${_minted?.apiKey} \\\n'
      'lycosa-agent run';

  String get _installCommand => _windows ? _installPowershell : _installBash;
  String get _command => _windows ? _runPowershell : _runBash;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add a node'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: _minted == null ? _mintForm() : _keyReveal(context),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(_minted == null ? 'Cancel' : 'Done'),
        ),
        if (_minted == null)
          FilledButton(
            onPressed: _busy ? null : _mint,
            child: _busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Create key'),
          ),
      ],
    );
  }

  Widget _mintForm() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Mint a node API key. Install the agent on the machine, '
          'then run it with this key — the node registers itself.',
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _name,
          decoration: const InputDecoration(
            labelText: 'Key name',
            hintText: 'garage-workstation',
          ),
          onSubmitted: (_) => _mint(),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ],
    );
  }

  Widget _keyReveal(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.warning_amber, color: LycosaColors.warning),
            const SizedBox(width: 8),
            const Expanded(
              child: Text('Copy this key now — it will not be shown again.'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        SelectableText(
          _minted!.apiKey,
          style: const TextStyle(fontFamily: 'monospace'),
        ),
        const SizedBox(height: 12),
        Text(
          _windows
              ? 'On the new machine (Windows PowerShell):'
              : 'On the new machine (macOS / Linux):',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 4),
        const Text('1. Install the agent (once per machine):'),
        const SizedBox(height: 4),
        _codeBlock(context, _installCommand),
        const SizedBox(height: 8),
        const Text('2. Register it and start:'),
        const SizedBox(height: 4),
        _codeBlock(context, _command, copyable: true),
        const SizedBox(height: 8),
        Text(
          _windows
              ? 'Paste each line separately. Replace the URL with the controller '
                    "PC's LAN IP (run ipconfig there) — localhost only works on "
                    'the controller itself.'
              : "Replace the URL with the controller's LAN IP if the agent runs "
                    'on a different machine — localhost only works on the '
                    'controller itself.',
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
            color: LycosaColors.warning,
          ),
        ),
      ],
    );
  }

  Widget _codeBlock(BuildContext context, String text, {bool copyable = false}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: LycosaColors.backgroundSecondary,
            border: Border.all(color: LycosaColors.border),
            borderRadius: BorderRadius.circular(10),
          ),
          child: SelectableText(
            text,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
        if (copyable)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              icon: const Icon(Icons.copy, size: 16),
              label: const Text('Copy'),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: text));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Copied to clipboard')),
                  );
                }
              },
            ),
          ),
      ],
    );
  }
}
