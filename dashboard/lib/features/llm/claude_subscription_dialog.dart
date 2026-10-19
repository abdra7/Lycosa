import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';

const claudeSignInCommand = 'claude auth login';

/// Connect your Claude plan through a node's own Claude Code (ADR-032).
///
/// Lycosa never signs in to Claude and never sees a Claude credential: you
/// sign in on the node with Anthropic's own flow, and the node runs Claude
/// Code for you only.
class ClaudeSubscriptionDialog extends ConsumerStatefulWidget {
  const ClaudeSubscriptionDialog({super.key, required this.provider});

  final LlmProvider provider;

  @override
  ConsumerState<ClaudeSubscriptionDialog> createState() =>
      _ClaudeSubscriptionDialogState();
}

class _ClaudeSubscriptionDialogState
    extends ConsumerState<ClaudeSubscriptionDialog> {
  final _label = TextEditingController(text: 'My Claude');
  List<ClaudeNode>? _nodes;
  String? _selected;
  bool _loading = false;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  ClaudeNode? get _node =>
      _nodes?.where((n) => n.nodeId == _selected).firstOrNull;

  Future<void> _load() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final nodes = await client.listClaudeSubscriptionNodes();
      if (!mounted) return;
      setState(() {
        _nodes = nodes;
        final keep = nodes.any((n) => n.nodeId == _selected);
        _selected = keep
            ? _selected
            : (nodes.where((n) => n.ready).firstOrNull ?? nodes.firstOrNull)
                  ?.nodeId;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _error = e.friendly);
      }
    } on ControllerUnreachableException catch (e) {
      if (mounted) {
        setState(() => _error = e.friendly);
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _connect() async {
    final client = ref.read(activeApiClientProvider);
    final node = _node;
    if (client == null || node == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await client.connectLlmAccount(
        provider: widget.provider.id,
        label: _label.text.trim(),
        nodeId: node.nodeId,
      );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on ControllerUnreachableException catch (e) {
      if (mounted) {
        setState(() => _error = e.friendly);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodySmall;
    final node = _node;
    return AlertDialog(
      title: const Text('Connect your Claude plan'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'A node runs Claude Code, signed in with your own Claude '
                'plan. Lycosa never sees your Claude sign-in, and only you '
                'can use this connection. Calls count toward your plan\'s '
                'usage limits.',
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Text(
                    'Nodes offering Claude',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const Spacer(),
                  TextButton.icon(
                    icon: const Icon(Icons.refresh, size: 18),
                    label: const Text('Re-check'),
                    onPressed: _loading ? null : _load,
                  ),
                ],
              ),
              if (_loading && _nodes == null)
                const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: CircularProgressIndicator()),
                )
              else if (_nodes != null && _nodes!.isEmpty)
                const _EnableHelp()
              else
                for (final n in _nodes ?? const <ClaudeNode>[])
                  _NodeTile(
                    node: n,
                    selected: n.nodeId == _selected,
                    onTap: () => setState(() => _selected = n.nodeId),
                  ),
              if (_loading && _nodes != null) const LinearProgressIndicator(),
              if (node != null && node.reachable && !node.signedIn) ...[
                const SizedBox(height: 8),
                _SignInHelp(nodeName: node.nodeName),
              ],
              const SizedBox(height: 12),
              TextField(
                controller: _label,
                decoration: const InputDecoration(labelText: 'Name'),
              ),
              const SizedBox(height: 8),
              Text(
                'Personal connection: it cannot be shared or used in '
                'deployment routes.',
                style: muted,
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
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
          onPressed: _busy || node == null || !node.ready ? null : _connect,
          child: _busy
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('Connect'),
        ),
      ],
    );
  }
}

class _NodeTile extends StatelessWidget {
  const _NodeTile({
    required this.node,
    required this.selected,
    required this.onTap,
  });

  final ClaudeNode node;
  final bool selected;
  final VoidCallback onTap;

  (Color, String) get _state {
    if (!node.reachable) {
      return (
        LycosaColors.error,
        node.detail.isEmpty ? 'Unreachable' : node.detail,
      );
    }
    if (!node.installed) {
      return (LycosaColors.error, 'Claude Code is not installed');
    }
    if (!node.ownerMatches) {
      return (LycosaColors.error, 'Serves another Lycosa user');
    }
    if (!node.signedIn) return (LycosaColors.warning, 'Not signed in');
    final plan = node.plan == null ? '' : ' · ${_capitalize(node.plan!)} plan';
    final how = node.authMethod == 'api_key' ? ' (API key)' : '';
    return (LycosaColors.success, 'Signed in$plan$how');
  }

  @override
  Widget build(BuildContext context) {
    final (color, text) = _state;
    final usage = [
      for (final w in node.planUsage)
        if (w.utilization != null)
          '${w.label}: ${(w.utilization! * 100).round()}% used',
    ];
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(
          color: selected
              ? Theme.of(context).colorScheme.primary
              : Colors.transparent,
          width: 2,
        ),
      ),
      child: ListTile(
        onTap: onTap,
        leading: Icon(
          selected ? Icons.radio_button_checked : Icons.radio_button_off,
        ),
        title: Text(node.nodeName),
        subtitle: Text(
          [
            text,
            if (node.version != null) 'Claude Code ${node.version}',
            if (usage.isNotEmpty) 'Plan usage · ${usage.join(' · ')}',
          ].join('\n'),
        ),
        trailing: Icon(Icons.circle, size: 12, color: color),
      ),
    );
  }
}

String _capitalize(String s) =>
    s.isEmpty ? s : '${s[0].toUpperCase()}${s.substring(1)}';

class _SignInHelp extends StatelessWidget {
  const _SignInHelp({required this.nodeName});

  final String nodeName;

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Sign in on $nodeName',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            const Text(
              'Open a terminal on that machine and run the command below. '
              'Anthropic\'s own sign-in opens in the browser; Lycosa is not '
              'involved. Then press Re-check.',
            ),
            const SizedBox(height: 8),
            _CopyableCommand(command: claudeSignInCommand),
          ],
        ),
      ),
    );
  }
}

class _EnableHelp extends StatelessWidget {
  const _EnableHelp();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('No node offers Claude yet. On the machine that should run it:'),
          SizedBox(height: 8),
          Text('1. Install Claude Code and sign in:'),
          _CopyableCommand(command: claudeSignInCommand),
          SizedBox(height: 8),
          Text('2. Set these for its Lycosa agent, then restart the agent:'),
          _CopyableCommand(
            command:
                'LYCOSA_CLAUDE_SUBSCRIPTION=true\n'
                'LYCOSA_CLAUDE_SUBSCRIPTION_OWNER=<your Lycosa email>',
          ),
        ],
      ),
    );
  }
}

class _CopyableCommand extends StatelessWidget {
  const _CopyableCommand({required this.command});

  final String command;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: SelectableText(
            command,
            style: const TextStyle(fontFamily: 'monospace'),
          ),
        ),
        IconButton(
          tooltip: 'Copy',
          icon: const Icon(Icons.copy, size: 18),
          onPressed: () {
            Clipboard.setData(ClipboardData(text: command));
            ScaffoldMessenger.maybeOf(
              context,
            )?.showSnackBar(const SnackBar(content: Text('Copied')));
          },
        ),
      ],
    );
  }
}
