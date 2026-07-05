import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';
import 'nodes_screen.dart';
import 'providers.dart';

class NodeDetailScreen extends ConsumerWidget {
  const NodeDetailScreen({super.key, required this.nodeId});

  final String nodeId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final node = ref.watch(nodeDetailProvider(nodeId));
    return Scaffold(
      appBar: AppBar(title: Text(node.value?.name ?? 'Node')),
      body: node.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => Center(child: Text('Failed to load node: $error')),
        data: (n) => SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Wrap(
            spacing: 16,
            runSpacing: 16,
            children: [
              _IdentityCard(node: n),
              _RoleCard(node: n),
              _MetricsCard(node: n),
              _ProfileCard(node: n),
            ],
          ),
        ),
      ),
    );
  }
}

class _CardShell extends StatelessWidget {
  const _CardShell({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 420,
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 12),
              child,
            ],
          ),
        ),
      ),
    );
  }
}

class _KeyValue extends StatelessWidget {
  const _KeyValue(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
              width: 130,
              child: Text(label, style: Theme.of(context).textTheme.bodySmall)),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }
}

class _IdentityCard extends StatelessWidget {
  const _IdentityCard({required this.node});

  final NodeInfo node;

  @override
  Widget build(BuildContext context) {
    return _CardShell(
      title: 'Node',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            StatusChip(status: node.status),
            const SizedBox(width: 8),
            Text('heartbeat ${heartbeatAge(node.lastHeartbeatAt)}'),
          ]),
          const SizedBox(height: 8),
          _KeyValue('ID', node.id),
          _KeyValue('Agent URL', node.agentUrl ?? '—'),
        ],
      ),
    );
  }
}

class _RoleCard extends ConsumerStatefulWidget {
  const _RoleCard({required this.node});

  final NodeInfo node;

  @override
  ConsumerState<_RoleCard> createState() => _RoleCardState();
}

class _RoleCardState extends ConsumerState<_RoleCard> {
  String? _selected;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _selected = widget.node.role ?? widget.node.recommendedRole;
  }

  Future<void> _save() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _selected == null) return;
    setState(() => _saving = true);
    try {
      await client.patchNode(widget.node.id, role: _selected);
      ref.invalidate(nodeDetailProvider(widget.node.id));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Role set to $_selected')));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.friendly)));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final node = widget.node;
    final confidence = ((node.recommendationConfidence ?? 0) * 100).round();
    return _CardShell(
      title: 'Role',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _KeyValue('Assigned', node.role ?? 'not assigned'),
          _KeyValue(
              'Recommended',
              node.recommendedRole != null
                  ? '${node.recommendedRole} ($confidence% confidence)'
                  : '—'),
          if (node.recommendationRationale.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Why:', style: Theme.of(context).textTheme.bodySmall),
            for (final reason in node.recommendationRationale)
              Padding(
                padding: const EdgeInsets.only(left: 8, top: 2),
                child: Text('• $reason'),
              ),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: _selected,
                  decoration: const InputDecoration(
                      labelText: 'Set role', isDense: true),
                  items: [
                    for (final role in nodeRoles)
                      DropdownMenuItem(value: role, child: Text(role)),
                  ],
                  onChanged: (v) => setState(() => _selected = v),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Save'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _MetricsCard extends StatelessWidget {
  const _MetricsCard({required this.node});

  final NodeInfo node;

  @override
  Widget build(BuildContext context) {
    final metrics = node.metrics;
    return _CardShell(
      title: 'Latest metrics',
      child: metrics == null
          ? const Text('No heartbeat received yet.')
          : Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _bar(context, 'CPU', (metrics['cpu_percent'] as num?)?.toDouble()),
                _bar(context, 'RAM', (metrics['ram_percent'] as num?)?.toDouble()),
                _bar(context, 'Disk', (metrics['disk_percent'] as num?)?.toDouble()),
                _KeyValue('Running tasks', '${metrics['running_tasks'] ?? 0}'),
                for (final (index, gpu)
                    in ((metrics['gpus'] as List?) ?? const []).indexed)
                  _KeyValue(
                    'GPU $index',
                    '${(gpu['util_percent'] as num?)?.toStringAsFixed(0) ?? '—'}% util'
                    ' · ${(gpu['temp_c'] as num?)?.toStringAsFixed(0) ?? '—'}°C',
                  ),
              ],
            ),
    );
  }

  Widget _bar(BuildContext context, String label, double? percent) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          SizedBox(
              width: 130,
              child: Text(label, style: Theme.of(context).textTheme.bodySmall)),
          Expanded(
            child: LinearProgressIndicator(
                value: (percent ?? 0) / 100, minHeight: 8),
          ),
          const SizedBox(width: 8),
          SizedBox(
              width: 44,
              child: Text(percent != null
                  ? '${percent.toStringAsFixed(0)}%'
                  : '—')),
        ],
      ),
    );
  }
}

class _ProfileCard extends StatelessWidget {
  const _ProfileCard({required this.node});

  final NodeInfo node;

  @override
  Widget build(BuildContext context) {
    final profile = node.hardwareProfile ?? const {};
    final runtimes = (profile['runtimes'] as List?) ?? const [];
    final gpus = (profile['gpus'] as List?) ?? const [];
    return _CardShell(
      title: 'Hardware profile',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _KeyValue('CPU', '${profile['cpu_model'] ?? '—'} '
              '(${node.cpuCores ?? '—'} cores)'),
          _KeyValue('RAM', node.ramGb != null ? '${node.ramGb} GB' : '—'),
          _KeyValue(
              'GPU',
              gpus.isEmpty
                  ? 'none'
                  : gpus
                      .map((g) => '${g['model']} (${g['vram_gb']} GB)')
                      .join(', ')),
          _KeyValue('Storage',
              node.storageGb != null ? '${node.storageGb} GB' : '—'),
          _KeyValue('OS', node.osName ?? '—'),
          _KeyValue(
              'Runtimes',
              runtimes.isEmpty
                  ? 'none detected'
                  : runtimes
                      .map((r) =>
                          '${r['name']}${(r['models'] as List?)?.isNotEmpty == true ? ' (${(r['models'] as List).length} models)' : ''}')
                      .join(', ')),
        ],
      ),
    );
  }
}
