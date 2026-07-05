import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/app_info.dart';
import '../../core/events.dart';
import '../../core/session.dart';
import '../admin/admin_screen.dart';
import '../knowledge/knowledge_screen.dart';
import '../nodes/nodes_screen.dart';
import '../nodes/providers.dart';
import '../tasks/tasks_screen.dart';
import '../workflows/workflows_screen.dart';

class _Section {
  const _Section(this.label, this.icon);
  final String label;
  final IconData icon;
}

const _sections = [
  _Section('Nodes', Icons.dns_outlined),
  _Section('Tasks', Icons.play_circle_outline),
  _Section('Workflows', Icons.account_tree_outlined),
  _Section('Knowledge', Icons.menu_book_outlined),
  _Section('Admin', Icons.admin_panel_settings_outlined),
];

/// Authenticated shell: nav rail + profile switcher + identity + logout,
/// plus the live event strip fed by the /events WebSocket (ADR-016).
class ShellScreen extends ConsumerStatefulWidget {
  const ShellScreen({super.key});

  @override
  ConsumerState<ShellScreen> createState() => _ShellScreenState();
}

class _ShellScreenState extends ConsumerState<ShellScreen> {
  int _selected = 0;
  LycosaEvent? _lastEvent;

  void _onEvent(AsyncValue<LycosaEvent>? _, AsyncValue<LycosaEvent> next) {
    final event = next.value;
    if (event == null) return;
    setState(() => _lastEvent = event);
    if (event.isNodeEvent) {
      // push beats polling: refresh the node list immediately
      ref.invalidate(nodesProvider);
    }
    if (event.isAlert && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(event.summary),
        backgroundColor: Theme.of(context).colorScheme.error,
        duration: const Duration(seconds: 6),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(eventsProvider, _onEvent);
    final session = ref.watch(sessionProvider).value!;
    final principal = session.principal!;
    final connected = ref.watch(eventsProvider).hasValue;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Lycosa'),
        actions: [
          if (session.profiles.length > 1)
            DropdownButton<String>(
              value: session.activeProfile!.id,
              underline: const SizedBox.shrink(),
              items: [
                for (final p in session.profiles)
                  DropdownMenuItem(value: p.id, child: Text(p.name)),
              ],
              onChanged: (id) {
                if (id != null) {
                  ref.read(sessionProvider.notifier).switchProfile(id);
                }
              },
            ),
          const SizedBox(width: 12),
          Chip(
            avatar: const Icon(Icons.person_outline, size: 18),
            label: Text('${principal.displayName} · ${principal.role}'),
          ),
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(sessionProvider.notifier).logout(),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _selected,
            onDestinationSelected: (i) => setState(() => _selected = i),
            labelType: NavigationRailLabelType.all,
            destinations: [
              for (final s in _sections)
                NavigationRailDestination(
                    icon: Icon(s.icon), label: Text(s.label)),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: switch (_selected) {
              0 => const NodesScreen(),
              1 => const TasksScreen(),
              2 => const WorkflowsScreen(),
              3 => const KnowledgeScreen(),
              _ => const AdminScreen(),
            },
          ),
        ],
      ),
      bottomNavigationBar: _EventStrip(connected: connected, event: _lastEvent),
    );
  }
}

class _EventStrip extends StatelessWidget {
  const _EventStrip({required this.connected, required this.event});

  final bool connected;
  final LycosaEvent? event;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            Icon(Icons.circle,
                size: 10, color: connected ? Colors.green : Colors.grey),
            const SizedBox(width: 6),
            Text(connected ? 'live' : 'connecting…',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(width: 16),
            Expanded(
              child: Text(
                event?.summary ?? '',
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            Text('v$appVersion', style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}
