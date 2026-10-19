import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../llm/providers.dart';
import 'chat_controller.dart';

/// Chat with any connected model, including your Claude plan through a
/// node's Claude Code (ADR-032). Answers stream in through Lycosa's LLM
/// layer; the conversation stays in this app session only.
class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _inputFocus = FocusNode();

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    _inputFocus.dispose();
    super.dispose();
  }

  void _send() {
    final text = _input.text;
    if (text.trim().isEmpty) return;
    _input.clear();
    ref.read(chatProvider.notifier).send(text);
    _inputFocus.requestFocus();
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });
  }

  /// Pick a sensible target once accounts load: your Claude plan if
  /// connected, else your first usable account, else the default route.
  void _ensureTarget(List<LlmAccount> usable, ChatState chat) {
    if (chat.targetChosen) return;
    if (chat.accountId != null && usable.any((a) => a.id == chat.accountId)) {
      return;
    }
    final preferred =
        usable.where((a) => a.isNodeRun).firstOrNull ?? usable.firstOrNull;
    if (preferred == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        ref
            .read(chatProvider.notifier)
            .selectTarget(preferred.id, null, auto: true);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final chat = ref.watch(chatProvider);
    final accounts = ref.watch(llmAccountsProvider);
    final usable = [
      for (final a in accounts.value ?? const <LlmAccount>[])
        if (a.usable && a.isActive) a,
    ];
    if (accounts.hasValue) _ensureTarget(usable, chat);
    ref.listen(chatProvider, (_, _) => _scrollToEnd());
    final account = usable.where((a) => a.id == chat.accountId).firstOrNull;

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Text('Chat', style: Theme.of(context).textTheme.headlineSmall),
              const Spacer(),
              _TargetPicker(usable: usable, chat: chat),
              IconButton(
                tooltip: 'New chat',
                icon: const Icon(Icons.add_comment_outlined),
                onPressed: chat.turns.isEmpty
                    ? null
                    : () => ref.read(chatProvider.notifier).clear(),
              ),
            ],
          ),
          Text(
            account == null
                ? 'Answers come from your default route (set it under '
                      'Providers → Routing & fallback).'
                : account.isNodeRun
                ? 'Runs Claude Code on ${account.nodeName ?? 'your node'} '
                      'with your own Claude plan; usage counts toward its '
                      'limits.'
                : '${account.providerName} · ${account.label}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          Expanded(
            child: Card(
              child: chat.turns.isEmpty
                  ? _EmptyChat(hasAccounts: usable.isNotEmpty)
                  : ListView.builder(
                      controller: _scroll,
                      padding: const EdgeInsets.all(12),
                      itemCount: chat.turns.length,
                      itemBuilder: (_, i) => _Bubble(turn: chat.turns[i]),
                    ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: CallbackShortcuts(
                  bindings: {
                    const SingleActivator(LogicalKeyboardKey.enter): () {
                      if (!chat.sending && chat.ready) _send();
                    },
                  },
                  child: TextField(
                    key: const Key('chat-input'),
                    controller: _input,
                    focusNode: _inputFocus,
                    minLines: 1,
                    maxLines: 6,
                    textInputAction: TextInputAction.newline,
                    decoration: const InputDecoration(
                      hintText:
                          'Message (Enter to send, Shift+Enter for a new line)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              chat.sending
                  ? OutlinedButton.icon(
                      icon: const Icon(Icons.stop),
                      label: const Text('Stop'),
                      onPressed: () => ref.read(chatProvider.notifier).stop(),
                    )
                  : FilledButton.icon(
                      icon: const Icon(Icons.send),
                      label: const Text('Send'),
                      onPressed: chat.ready ? _send : null,
                    ),
            ],
          ),
        ],
      ),
    );
  }
}

class _TargetPicker extends ConsumerWidget {
  const _TargetPicker({required this.usable, required this.chat});

  final List<LlmAccount> usable;
  final ChatState chat;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final controller = ref.read(chatProvider.notifier);
    final accountId = usable.any((a) => a.id == chat.accountId)
        ? chat.accountId
        : null;
    final models = accountId == null
        ? null
        : ref.watch(chatModelsProvider(accountId));
    final modelIds = [for (final m in models?.value ?? const <LlmModel>[]) m];
    if (accountId != null &&
        modelIds.isNotEmpty &&
        !modelIds.any((m) => m.id == chat.model)) {
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => controller.selectTarget(
          accountId,
          modelIds.first.id,
          auto: !chat.targetChosen,
        ),
      );
    }
    return Wrap(
      spacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        DropdownButton<String?>(
          value: accountId,
          hint: const Text('Default route'),
          items: [
            const DropdownMenuItem<String?>(
              value: null,
              child: Text('Default route'),
            ),
            for (final a in usable)
              DropdownMenuItem<String?>(
                value: a.id,
                child: Text('${a.providerName} · ${a.label}'),
              ),
          ],
          onChanged: chat.sending
              ? null
              : (id) => controller.selectTarget(id, null),
        ),
        if (accountId != null)
          models == null || models.isLoading
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : DropdownButton<String>(
                  value: modelIds.any((m) => m.id == chat.model)
                      ? chat.model
                      : null,
                  hint: Text(models.hasError ? 'Models unavailable' : 'Model'),
                  items: [
                    for (final m in modelIds)
                      DropdownMenuItem(
                        value: m.id,
                        child: Text(m.displayName ?? m.id),
                      ),
                  ],
                  onChanged: chat.sending
                      ? null
                      : (id) => controller.selectTarget(accountId, id),
                ),
      ],
    );
  }
}

class _EmptyChat extends StatelessWidget {
  const _EmptyChat({required this.hasAccounts});

  final bool hasAccounts;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodyMedium;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.chat_bubble_outline,
              size: 40,
              color: Theme.of(context).colorScheme.primary,
            ),
            const SizedBox(height: 12),
            Text(
              hasAccounts
                  ? 'Ask anything. Answers stream in from the model you pick.'
                  : 'Connect a model first: open Providers and connect an '
                        'account. To use your Claude plan, connect "Claude '
                        'subscription".',
              textAlign: TextAlign.center,
              style: muted,
            ),
          ],
        ),
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.turn});

  final ChatTurn turn;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final mine = turn.role == 'user';
    final meta = [
      if (turn.model != null) turn.model!,
      if (turn.totalTokens != null) '${turn.totalTokens} tokens',
    ].join(' · ');
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: turn.failed
                ? scheme.errorContainer
                : mine
                ? scheme.primaryContainer
                : scheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (turn.text.isNotEmpty || turn.streaming)
                SelectableText(
                  turn.streaming ? '${turn.text}▍' : turn.text,
                  style: TextStyle(
                    color: mine ? scheme.onPrimaryContainer : scheme.onSurface,
                  ),
                ),
              if (turn.failed)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.error_outline,
                      size: 16,
                      color: scheme.onErrorContainer,
                    ),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        turn.error!,
                        style: TextStyle(color: scheme.onErrorContainer),
                      ),
                    ),
                  ],
                ),
              if (!mine && !turn.streaming && !turn.failed)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (meta.isNotEmpty)
                      Text(meta, style: Theme.of(context).textTheme.bodySmall),
                    IconButton(
                      tooltip: 'Copy',
                      visualDensity: VisualDensity.compact,
                      iconSize: 16,
                      icon: const Icon(Icons.copy),
                      onPressed: () =>
                          Clipboard.setData(ClipboardData(text: turn.text)),
                    ),
                  ],
                ),
            ],
          ),
        ),
      ),
    );
  }
}
