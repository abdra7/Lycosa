import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';
import 'add_node_dialog.dart';
import 'node_detail_screen.dart';
import 'providers.dart';

String heartbeatAge(DateTime? last) {
  if (last == null) return '—';
  final delta = DateTime.now().toUtc().difference(last.toUtc());
  if (delta.inSeconds < 60) return '${delta.inSeconds}s ago';
  if (delta.inMinutes < 60) return '${delta.inMinutes}m ago';
  if (delta.inHours < 24) return '${delta.inHours}h ago';
  return '${delta.inDays}d ago';
}

Color statusColor(BuildContext context, String status) => switch (status) {
      'online' => Colors.green,
      'offline' => Theme.of(context).colorScheme.error,
      _ => Colors.orange, // registered
    };

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final color = statusColor(context, status);
    return Chip(
      avatar: Icon(Icons.circle, size: 10, color: color),
      label: Text(status),
      visualDensity: VisualDensity.compact,
    );
  }
}

class NodesScreen extends ConsumerWidget {
  const NodesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    final principal = ref.watch(sessionProvider).value?.principal;

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Nodes', style: Theme.of(context).textTheme.headlineSmall),
              const Spacer(),
              IconButton(
                tooltip: 'Refresh',
                icon: const Icon(Icons.refresh),
                onPressed: () => ref.invalidate(nodesProvider),
              ),
              if (principal?.role == 'admin')
                FilledButton.icon(
                  icon: const Icon(Icons.add),
                  label: const Text('Add node'),
                  onPressed: () => showDialog(
                    context: context,
                    builder: (_) => const AddNodeDialog(),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          Expanded(
            child: nodes.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (error, _) => Center(child: Text('Failed to load nodes: $error')),
              data: (list) => list.isEmpty
                  ? const Center(
                      child: Text(
                          'No nodes yet. Add one and run lycosa-agent on the machine.'))
                  : _NodesTable(nodes: list),
            ),
          ),
        ],
      ),
    );
  }
}

class _NodesTable extends StatelessWidget {
  const _NodesTable({required this.nodes});

  final List<NodeInfo> nodes;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: SizedBox(
        width: double.infinity,
        child: DataTable(
          showCheckboxColumn: false,
          columns: const [
            DataColumn(label: Text('Name')),
            DataColumn(label: Text('Status')),
            DataColumn(label: Text('Role')),
            DataColumn(label: Text('Recommended')),
            DataColumn(label: Text('Heartbeat')),
            DataColumn(label: Text('CPU')),
            DataColumn(label: Text('RAM')),
          ],
          rows: [
            for (final node in nodes)
              DataRow(
                onSelectChanged: (_) => Navigator.of(context).push(
                  MaterialPageRoute(
                      builder: (_) => NodeDetailScreen(nodeId: node.id)),
                ),
                cells: [
                  DataCell(Text(node.name)),
                  DataCell(StatusChip(status: node.status)),
                  DataCell(Text(node.role ?? '—')),
                  DataCell(Text(node.recommendedRole != null
                      ? '${node.recommendedRole} '
                          '(${((node.recommendationConfidence ?? 0) * 100).round()}%)'
                      : '—')),
                  DataCell(Text(heartbeatAge(node.lastHeartbeatAt))),
                  DataCell(Text(_metric(node, 'cpu_percent', suffix: '%'))),
                  DataCell(Text(_metric(node, 'ram_percent', suffix: '%'))),
                ],
              ),
          ],
        ),
      ),
    );
  }

  String _metric(NodeInfo node, String key, {String suffix = ''}) {
    final value = node.metrics?[key];
    return value == null ? '—' : '${(value as num).toStringAsFixed(0)}$suffix';
  }
}
