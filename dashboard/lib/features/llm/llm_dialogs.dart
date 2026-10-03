import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';

/// Capability chips: ✓ supported, ✗ not supported, ? unknown (never guessed).
class CapabilityChips extends StatelessWidget {
  const CapabilityChips(this.capabilities, {super.key});

  final LlmCapabilities capabilities;

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.labelSmall;
    return Wrap(
      spacing: 4,
      runSpacing: 4,
      children: [
        for (final entry in LlmCapabilities.labels.entries)
          Tooltip(
            message: switch (capabilities[entry.key]) {
              true => '${entry.value}: supported',
              false => '${entry.value}: not supported',
              null => '${entry.value}: unknown (provider does not say)',
            },
            child: Chip(
              visualDensity: VisualDensity.compact,
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
              label: Text(
                '${switch (capabilities[entry.key]) {
                  true => '✓',
                  false => '✗',
                  null => '?',
                }} ${entry.value}',
                style: style,
              ),
            ),
          ),
      ],
    );
  }
}

String _errorText(Object e) => switch (e) {
  ApiException() => e.friendly,
  ControllerUnreachableException() => e.friendly,
  _ => 'Request failed',
};

/// Models the provider reports for one account, with documented
/// capabilities; set one as your default or test it.
class ModelsDialog extends ConsumerStatefulWidget {
  const ModelsDialog({super.key, required this.account});

  final LlmAccount account;

  @override
  ConsumerState<ModelsDialog> createState() => _ModelsDialogState();
}

class _ModelsDialogState extends ConsumerState<ModelsDialog> {
  List<LlmModel>? _models;
  String? _message;
  bool _busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool refresh = false}) async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() => _busy = true);
    try {
      final models = await client.listLlmAccountModels(
        widget.account.id,
        refresh: refresh,
      );
      if (mounted) setState(() => _models = models);
    } catch (e) {
      if (mounted) setState(() => _message = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _makeDefault(LlmModel model) async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    try {
      final routing = await client.getLlmRouting();
      final current = routing.routeFor('default', 'personal')?.chain ?? [];
      final chain = [
        LlmRouteEntry(accountId: model.accountId, model: model.id),
        ...current.where(
          (e) => !(e.accountId == model.accountId && e.model == model.id),
        ),
      ].take(5).toList();
      await client.setLlmRoute('default', chain);
      setState(() => _message = '${model.ref} is now your default model.');
    } catch (e) {
      setState(() => _message = _errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final models = _models;
    return AlertDialog(
      title: Text('Models · ${widget.account.label}'),
      content: SizedBox(
        width: 640,
        height: 460,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_busy) const LinearProgressIndicator(),
            if (_message != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(_message!),
              ),
            Expanded(
              child: models == null
                  ? const SizedBox.shrink()
                  : models.isEmpty
                  ? const Center(
                      child: Text('The provider reported no models.'),
                    )
                  : ListView(
                      children: [
                        for (final m in models)
                          ListTile(
                            title: Text(m.displayName ?? m.id),
                            subtitle: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  [
                                    m.ref,
                                    if (m.contextWindow != null)
                                      '${m.contextWindow} token context',
                                  ].join(' · '),
                                ),
                                const SizedBox(height: 4),
                                CapabilityChips(m.capabilities),
                              ],
                            ),
                            trailing: TextButton(
                              onPressed: () => _makeDefault(m),
                              child: const Text('Set as default'),
                            ),
                          ),
                      ],
                    ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => _load(refresh: true),
          child: const Text('Refresh'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Close'),
        ),
      ],
    );
  }
}

/// Edit the route (primary + fallbacks) for one purpose.
class RoutingDialog extends ConsumerStatefulWidget {
  const RoutingDialog({
    super.key,
    required this.accounts,
    required this.routing,
    required this.isAdmin,
  });

  final List<LlmAccount> accounts;
  final LlmRouting routing;
  final bool isAdmin;

  @override
  ConsumerState<RoutingDialog> createState() => _RoutingDialogState();
}

class _RoutingDialogState extends ConsumerState<RoutingDialog> {
  String _purpose = 'default';
  String _scope = 'personal';
  final List<(String?, TextEditingController)> _rows = [];
  String? _message;
  bool _busy = false;

  static const _descriptions = {
    'default': 'Default model; later entries are fallbacks.',
    'coding': 'Coding tasks (route "auto" with type coding).',
    'reasoning': 'Reasoning-heavy work.',
    'vision': 'Image input (route "auto" with type vision).',
    'cheap': 'Lower-cost model for bulk work.',
    'private': 'Local runtimes only; prompts never leave your network.',
    'offline': 'Local runtimes only, for working without internet.',
  };

  @override
  void initState() {
    super.initState();
    _loadRows();
  }

  @override
  void dispose() {
    for (final (_, c) in _rows) {
      c.dispose();
    }
    super.dispose();
  }

  List<LlmAccount> get _eligible => widget.accounts.where((a) {
    if (!a.usable) return false;
    if (_scope == 'deployment' && !a.isShared) return false;
    if ((_purpose == 'private' || _purpose == 'offline') && !a.isLocal) {
      return false;
    }
    return true;
  }).toList();

  void _loadRows() {
    for (final (_, c) in _rows) {
      c.dispose();
    }
    _rows.clear();
    final route = widget.routing.routeFor(_purpose, _scope);
    for (final entry in route?.chain ?? const <LlmRouteEntry>[]) {
      _rows.add((entry.accountId, TextEditingController(text: entry.model)));
    }
    if (_rows.isEmpty) _rows.add((null, TextEditingController()));
  }

  Future<void> _save() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    final chain = [
      for (final (account, model) in _rows)
        if (account != null && model.text.trim().isNotEmpty)
          LlmRouteEntry(accountId: account, model: model.text.trim()),
    ];
    if (chain.isEmpty) {
      setState(() => _message = 'Add at least one account and model.');
      return;
    }
    setState(() => _busy = true);
    try {
      await client.setLlmRoute(_purpose, chain, scope: _scope);
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      setState(() => _message = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _remove() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() => _busy = true);
    try {
      await client.deleteLlmRoute(_purpose, scope: _scope);
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      setState(() => _message = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final eligible = _eligible;
    return AlertDialog(
      title: const Text('Routing & fallback'),
      content: SizedBox(
        width: 620,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: DropdownButtonFormField<String>(
                      initialValue: _purpose,
                      decoration: const InputDecoration(labelText: 'Purpose'),
                      items: [
                        for (final p in widget.routing.purposes)
                          DropdownMenuItem(value: p, child: Text(p)),
                      ],
                      onChanged: (v) => setState(() {
                        _purpose = v!;
                        _loadRows();
                      }),
                    ),
                  ),
                  if (widget.isAdmin) ...[
                    const SizedBox(width: 12),
                    Expanded(
                      child: DropdownButtonFormField<String>(
                        initialValue: _scope,
                        decoration: const InputDecoration(labelText: 'Scope'),
                        items: const [
                          DropdownMenuItem(
                            value: 'personal',
                            child: Text('My route'),
                          ),
                          DropdownMenuItem(
                            value: 'deployment',
                            child: Text('Deployment default'),
                          ),
                        ],
                        onChanged: (v) => setState(() {
                          _scope = v!;
                          _loadRows();
                        }),
                      ),
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 8),
              Text(
                _descriptions[_purpose] ?? '',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 12),
              for (var i = 0; i < _rows.length; i++)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 72,
                        child: Text(i == 0 ? 'Primary' : 'Fallback $i'),
                      ),
                      Expanded(
                        flex: 3,
                        child: DropdownButtonFormField<String>(
                          initialValue: eligible.any((a) => a.id == _rows[i].$1)
                              ? _rows[i].$1
                              : null,
                          isExpanded: true,
                          hint: const Text('Account'),
                          items: [
                            for (final a in eligible)
                              DropdownMenuItem(
                                value: a.id,
                                child: Text('${a.providerName} · ${a.label}'),
                              ),
                          ],
                          onChanged: (v) =>
                              setState(() => _rows[i] = (v, _rows[i].$2)),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        flex: 2,
                        child: TextField(
                          controller: _rows[i].$2,
                          decoration: const InputDecoration(
                            hintText: 'Model id',
                            isDense: true,
                          ),
                        ),
                      ),
                      IconButton(
                        tooltip: 'Remove entry',
                        icon: const Icon(Icons.close, size: 18),
                        onPressed: _rows.length == 1
                            ? null
                            : () => setState(() {
                                _rows.removeAt(i).$2.dispose();
                              }),
                      ),
                    ],
                  ),
                ),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  icon: const Icon(Icons.add),
                  label: const Text('Add fallback'),
                  onPressed: _rows.length >= 5
                      ? null
                      : () => setState(
                          () => _rows.add((null, TextEditingController())),
                        ),
                ),
              ),
              if (eligible.isEmpty)
                const Text('No eligible accounts for this purpose/scope yet.'),
              if (_busy) const LinearProgressIndicator(),
              if (_message != null)
                Text(
                  _message!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
            ],
          ),
        ),
      ),
      actions: [
        if (widget.routing.routeFor(_purpose, _scope) != null)
          TextButton(
            onPressed: _busy ? null : _remove,
            child: const Text('Remove route'),
          ),
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _busy ? null : _save,
          child: const Text('Save route'),
        ),
      ],
    );
  }
}

/// Send one prompt through a route or account and show who answered.
class PromptTestDialog extends ConsumerStatefulWidget {
  const PromptTestDialog({super.key, this.account});

  final LlmAccount? account; // null = use a routing purpose

  @override
  ConsumerState<PromptTestDialog> createState() => _PromptTestDialogState();
}

class _PromptTestDialogState extends ConsumerState<PromptTestDialog> {
  final _prompt = TextEditingController(text: 'Reply with one short sentence.');
  final _model = TextEditingController();
  String _purpose = 'default';
  LlmAnswer? _answer;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _prompt.dispose();
    _model.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _answer = null;
    });
    try {
      final answer = await client.llmTestPrompt(
        prompt: _prompt.text,
        accountId: widget.account?.id,
        model: widget.account != null ? _model.text.trim() : null,
        purpose: widget.account == null ? _purpose : null,
      );
      setState(() => _answer = answer);
    } catch (e) {
      setState(() => _error = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final answer = _answer;
    return AlertDialog(
      title: Text(
        widget.account == null
            ? 'Test a route'
            : 'Test ${widget.account!.label}',
      ),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (widget.account == null)
                DropdownButtonFormField<String>(
                  initialValue: _purpose,
                  decoration: const InputDecoration(labelText: 'Route'),
                  items: [
                    for (final p in const [
                      'default',
                      'coding',
                      'reasoning',
                      'vision',
                      'cheap',
                      'private',
                      'offline',
                    ])
                      DropdownMenuItem(value: p, child: Text(p)),
                  ],
                  onChanged: (v) => setState(() => _purpose = v!),
                )
              else
                TextField(
                  controller: _model,
                  decoration: const InputDecoration(labelText: 'Model id'),
                ),
              const SizedBox(height: 12),
              TextField(
                controller: _prompt,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'Prompt'),
              ),
              const SizedBox(height: 8),
              Text(
                'This sends a real request; the provider may charge for it.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              if (_busy) const LinearProgressIndicator(),
              if (_error != null)
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              if (answer != null) ...[
                const SizedBox(height: 12),
                SelectableText(answer.content),
                const SizedBox(height: 8),
                Text(
                  [
                    '${answer.provider}:${answer.model}',
                    if (answer.fallbackIndex > 0)
                      'fallback #${answer.fallbackIndex}',
                    '${answer.latencyMs} ms',
                    if (answer.totalTokens != null)
                      '${answer.totalTokens} tokens',
                    answer.estimatedCost != null
                        ? 'cost ${answer.estimatedCost}'
                        : 'cost unknown',
                  ].join(' · '),
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: answer.fallbackIndex > 0
                        ? LycosaColors.warning
                        : null,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Close'),
        ),
        FilledButton(
          onPressed: _busy ? null : _send,
          child: const Text('Send'),
        ),
      ],
    );
  }
}
