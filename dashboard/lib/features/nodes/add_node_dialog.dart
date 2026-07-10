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

  static const _installCommand =
      'curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash';

  String get _command {
    final baseUrl =
        ref.read(sessionProvider).value?.activeProfile?.baseUrl ??
        'http://<controller>:8000';
    return 'LYCOSA_CONTROLLER_URL=$baseUrl \\\n'
        'LYCOSA_API_KEY=${_minted?.apiKey} \\\n'
        'lycosa-agent run';
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add a node'),
      content: SizedBox(
        width: 520,
        child: _minted == null ? _mintForm() : _keyReveal(context),
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
        const Text('Install the agent (once per machine):'),
        const SizedBox(height: 4),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: LycosaColors.backgroundSecondary,
            border: Border.all(color: LycosaColors.border),
            borderRadius: BorderRadius.circular(10),
          ),
          child: const SelectableText(
            _installCommand,
            style: TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
        const SizedBox(height: 12),
        const Text('Run on the new machine:'),
        const SizedBox(height: 4),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: LycosaColors.backgroundSecondary,
            border: Border.all(color: LycosaColors.border),
            borderRadius: BorderRadius.circular(10),
          ),
          child: SelectableText(
            _command,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
        const SizedBox(height: 8),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton.icon(
            icon: const Icon(Icons.copy, size: 16),
            label: const Text('Copy command'),
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: _command));
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
