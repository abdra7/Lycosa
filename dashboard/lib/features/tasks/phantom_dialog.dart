import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/session.dart';

/// Deliberately separate from task history, provider selection and RAG forms.
class PhantomDialog extends ConsumerStatefulWidget {
  const PhantomDialog({super.key});
  @override
  ConsumerState<PhantomDialog> createState() => _PhantomDialogState();
}

class _PhantomDialogState extends ConsumerState<PhantomDialog> {
  final _prompt = TextEditingController();
  List<String> _models = [];
  String? _model;
  String? _output;
  String? _error;
  bool _ready = false;
  bool _busy = false;
  Timer? _expiry;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    try {
      final info = await client.phantomCapabilities();
      if (!mounted) return;
      setState(() {
        _models = List<String>.from(info['models'] as List);
        _model = _models.isEmpty ? null : _models.first;
        _ready = info['enabled'] == true && _models.isNotEmpty;
        if (!_ready) _error = 'Ask the controller administrator to configure an isolated local model.';
      });
    } catch (_) {
      if (mounted) setState(() => _error = 'Phantom execution is unavailable.');
    }
  }

  void _clear() {
    _expiry?.cancel();
    _prompt.clear();
    if (mounted) setState(() => _output = null);
  }

  Future<void> _run() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _model == null || _prompt.text.trim().isEmpty) return;
    _expiry?.cancel();
    var prompt = _prompt.text;
    _prompt.clear();
    setState(() { _busy = true; _output = null; _error = null; });
    try {
      final result = await client.submitPhantom(prompt: prompt, model: _model!);
      if (!mounted || ref.read(activeApiClientProvider) != client) return;
      setState(() => _output = result['output'] as String);
      _expiry = Timer(const Duration(seconds: 60), _clear);
    } catch (_) {
      if (mounted) setState(() => _error =
        'Phantom did not return a confirmed result. No history lookup is available. Ask the administrator to check isolation and cleanup.');
    } finally {
      prompt = '';
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _expiry?.cancel();
    _prompt.clear();
    _prompt.dispose();
    _output = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(activeApiClientProvider, (previous, next) {
      if (previous != next && mounted) Navigator.of(context).pop();
    });
    return AlertDialog(
      title: const Text('Ephemeral Phantom Agent'),
      content: SizedBox(width: 620, child: SingleChildScrollView(child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text('Runs one isolated local model without cloud access or task history. '
            'The result is shown once and cleared after 60 seconds. '
            'This reduces retained content; it does not guarantee forensic erasure from RAM or your device.'),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            key: ValueKey(_models.join(',')),
            initialValue: _model,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Isolated model'),
            items: [for (final m in _models) DropdownMenuItem(value: m, child: Text(m))],
            onChanged: _busy ? null : (v) => setState(() => _model = v),
          ),
          const SizedBox(height: 12),
          TextField(controller: _prompt, minLines: 3, maxLines: 8,
            enabled: _ready && !_busy, enableSuggestions: false, autocorrect: false,
            decoration: const InputDecoration(labelText: 'Sensitive task input')),
          if (_busy) const LinearProgressIndicator(),
          if (_error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!)),
          if (_output != null) Padding(padding: const EdgeInsets.only(top: 12),
            child: Text(_output!)), // No clipboard/export action for transient output.
        ],
      ))),
      actions: [
        TextButton(onPressed: _clear, child: const Text('Clear')),
        FilledButton(onPressed: _ready && !_busy ? _run : null, child: const Text('Run Phantom')),
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Close')),
      ],
    );
  }
}
