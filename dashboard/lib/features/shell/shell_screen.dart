import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/session.dart';
import '../admin/admin_screen.dart';
import '../knowledge/knowledge_screen.dart';
import '../nodes/nodes_screen.dart';
import '../tasks/tasks_screen.dart';
import '../workflows/workflows_screen.dart';

class _Section {
  const _Section(this.label, this.icon, this.owner);
  final String label;
  final IconData icon;
  final String owner; // which sub-phase delivers the real screen
}

const _sections = [
  _Section('Nodes', Icons.dns_outlined, '8b'),
  _Section('Tasks', Icons.play_circle_outline, '8c'),
  _Section('Workflows', Icons.account_tree_outlined, '8c'),
  _Section('Knowledge', Icons.menu_book_outlined, '8d'),
  _Section('Admin', Icons.admin_panel_settings_outlined, '8d'),
];

/// Authenticated shell: nav rail + profile switcher + identity + logout.
class ShellScreen extends ConsumerStatefulWidget {
  const ShellScreen({super.key});

  @override
  ConsumerState<ShellScreen> createState() => _ShellScreenState();
}

class _ShellScreenState extends ConsumerState<ShellScreen> {
  int _selected = 0;

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider).value!;
    final principal = session.principal!;

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
    );
  }
}
