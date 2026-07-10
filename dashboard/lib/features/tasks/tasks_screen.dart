import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';
import 'providers.dart';

const _taskTypes = ['auto', 'coding', 'retrieval', 'tool', 'vision', 'general'];

Color taskStatusColor(BuildContext context, String status) =>
    LycosaColors.status(status);

class TasksScreen extends ConsumerStatefulWidget {
  const TasksScreen({super.key});

  @override
  ConsumerState<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends ConsumerState<TasksScreen> {
  final _prompt = TextEditingController();
  final _model = TextEditingController();
  final _knowledgeQuery = TextEditingController();
  String _type = 'auto';
  bool _busy = false;
  TaskInfo? _lastResult;
  String? _error;

  @override
  void dispose() {
    for (final c in [_prompt, _model, _knowledgeQuery]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _prompt.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
      _lastResult = null;
    });
    try {
      final task = await client.submitTask(
        prompt: _prompt.text.trim(),
        type: _type == 'auto' ? null : _type,
        model: _model.text.trim().isEmpty ? null : _model.text.trim(),
        knowledgeQuery: _knowledgeQuery.text.trim().isEmpty
            ? null
            : _knowledgeQuery.text.trim(),
      );
      setState(() => _lastResult = task);
      ref.invalidate(tasksProvider);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on ControllerUnreachableException catch (e) {
      // the controller records the outcome even if we drop the connection,
      // so the task will surface in Recent once it finishes
      setState(
        () => _error =
            '${e.friendly} — the task may still be running; watch Recent below.',
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tasks = ref.watch(tasksProvider);
    // One scrollable page: the submit card can grow (long prompts, result
    // panel) without ever overflowing the window.
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Tasks', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 12),
        _submitCard(context),
        const SizedBox(height: 16),
        Text('Recent', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        ...tasks.when(
          loading: () => const [
            Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
          ],
          error: (e, _) => [
            Padding(
              padding: const EdgeInsets.all(24),
              child: Center(child: Text('Failed to load tasks: $e')),
            ),
          ],
          data: (list) => list.isEmpty
              ? const [
                  Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: Text('No tasks yet.')),
                  ),
                ]
              : [for (final t in list) _TaskTile(task: t)],
        ),
      ],
    );
  }

  Widget _submitCard(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _prompt,
              maxLines: 3,
              minLines: 1,
              decoration: const InputDecoration(
                labelText: 'Prompt',
                hintText: 'What should the fabric do?',
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                DropdownButton<String>(
                  value: _type,
                  items: [
                    for (final t in _taskTypes)
                      DropdownMenuItem(value: t, child: Text('type: $t')),
                  ],
                  onChanged: (v) => setState(() => _type = v ?? 'auto'),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _model,
                    decoration: const InputDecoration(
                      labelText: 'Model (optional)',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _knowledgeQuery,
                    decoration: const InputDecoration(
                      labelText: 'Knowledge query (optional)',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                FilledButton.icon(
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Run task'),
                  onPressed: _busy ? null : _submit,
                ),
              ],
            ),
            if (_busy)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: LinearProgressIndicator(),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            if (_lastResult != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: _ResultPanel(task: _lastResult!),
              ),
          ],
        ),
      ),
    );
  }
}

/// Long model output in a bounded, independently scrollable block so a big
/// result can never blow up the page layout. Copy button included because
/// scroll-to-select is painful for multi-page output.
class _OutputBlock extends StatefulWidget {
  const _OutputBlock({required this.text, this.isError = false});

  final String text;
  final bool isError;

  @override
  State<_OutputBlock> createState() => _OutputBlockState();
}

class _OutputBlockState extends State<_OutputBlock> {
  final _scroll = ScrollController();

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = widget.text;
    return Stack(
      children: [
        Container(
          width: double.infinity,
          constraints: const BoxConstraints(maxHeight: 280),
          decoration: BoxDecoration(
            color: scheme.surfaceContainerLow,
            border: Border.all(color: scheme.outline),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Scrollbar(
            controller: _scroll,
            thumbVisibility: true,
            child: SingleChildScrollView(
              controller: _scroll,
              padding: const EdgeInsets.fromLTRB(12, 12, 40, 12),
              child: SelectableText(
                text,
                style: TextStyle(
                  fontSize: 13,
                  height: 1.5,
                  color: widget.isError ? scheme.error : scheme.onSurface,
                ),
              ),
            ),
          ),
        ),
        Positioned(
          top: 4,
          right: 4,
          child: IconButton(
            tooltip: 'Copy',
            icon: const Icon(Icons.copy, size: 16),
            visualDensity: VisualDensity.compact,
            onPressed: () {
              Clipboard.setData(ClipboardData(text: text));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('Copied to clipboard'),
                  duration: Duration(seconds: 2),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _ResultPanel extends StatelessWidget {
  const _ResultPanel({required this.task});

  final TaskInfo task;

  @override
  Widget build(BuildContext context) {
    final color = taskStatusColor(context, task.status);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: color),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.circle, size: 10, color: color),
              const SizedBox(width: 6),
              Text('${task.status} · ${task.type}'),
            ],
          ),
          const SizedBox(height: 8),
          _OutputBlock(
            text: task.output ?? task.error ?? '(no output)',
            isError: task.output == null && task.error != null,
          ),
        ],
      ),
    );
  }
}

class _TaskTile extends StatelessWidget {
  const _TaskTile({required this.task});

  final TaskInfo task;

  @override
  Widget build(BuildContext context) {
    final color = taskStatusColor(context, task.status);
    return Card(
      child: ExpansionTile(
        leading: Icon(Icons.circle, size: 12, color: color),
        title: Text(task.prompt, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          '${task.status} · ${task.type}'
          '${task.executions.isNotEmpty ? ' · ${task.executions.length} attempt(s)' : ''}',
        ),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (task.output != null) ...[
            Text('Output', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            _OutputBlock(text: task.output!),
            const SizedBox(height: 8),
          ],
          if (task.error != null) ...[
            Text('Error', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            _OutputBlock(text: task.error!, isError: true),
            const SizedBox(height: 8),
          ],
          if (task.executions.isNotEmpty) ...[
            Text(
              'Execution trace',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            for (final execution in task.executions)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'attempt ${execution.attempt}: ${execution.status}'
                  '${execution.error != null ? ' — ${execution.error}' : ''}',
                ),
              ),
          ],
        ],
      ),
    );
  }
}
