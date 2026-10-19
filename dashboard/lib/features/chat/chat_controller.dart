import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';

/// One message in the conversation. Assistant turns fill in while streaming.
class ChatTurn {
  const ChatTurn({
    required this.role,
    this.text = '',
    this.streaming = false,
    this.error,
    this.provider,
    this.model,
    this.totalTokens,
  });

  final String role; // user | assistant
  final String text;
  final bool streaming;
  final String? error;
  final String? provider;
  final String? model;
  final int? totalTokens;

  bool get failed => error != null;

  ChatTurn copyWith({
    String? text,
    bool? streaming,
    String? error,
    String? provider,
    String? model,
    int? totalTokens,
  }) => ChatTurn(
    role: role,
    text: text ?? this.text,
    streaming: streaming ?? this.streaming,
    error: error ?? this.error,
    provider: provider ?? this.provider,
    model: model ?? this.model,
    totalTokens: totalTokens ?? this.totalTokens,
  );
}

class ChatState {
  const ChatState({
    this.turns = const [],
    this.accountId,
    this.model,
    this.targetChosen = false,
  });

  final List<ChatTurn> turns;

  /// null = the default route (personal default, else the deployment one)
  final String? accountId;
  final String? model;

  /// the user picked the target; automatic preselection stops
  final bool targetChosen;

  bool get sending => turns.isNotEmpty && turns.last.streaming;

  /// An explicit account needs its model before a message can go out.
  bool get ready => accountId == null || model != null;

  ChatState copyWith({
    List<ChatTurn>? turns,
    String? Function()? accountId,
    String? Function()? model,
    bool? targetChosen,
  }) => ChatState(
    turns: turns ?? this.turns,
    accountId: accountId != null ? accountId() : this.accountId,
    model: model != null ? model() : this.model,
    targetChosen: targetChosen ?? this.targetChosen,
  );
}

/// The conversation lives here, not in the screen, so it survives switching
/// sections; it resets when the active controller profile changes.
class ChatController extends Notifier<ChatState> {
  StreamSubscription<LlmChatEvent>? _subscription;
  Completer<void>? _done;

  @override
  ChatState build() {
    ref.watch(activeApiClientProvider);
    ref.onDispose(() => _subscription?.cancel());
    return const ChatState();
  }

  /// [auto] marks a preselection, which a later user choice overrides.
  void selectTarget(String? accountId, String? model, {bool auto = false}) {
    state = state.copyWith(
      accountId: () => accountId,
      model: () => model,
      targetChosen: auto ? null : true,
    );
  }

  void clear() {
    _cancel();
    state = state.copyWith(turns: const []);
  }

  void stop() {
    _cancel();
    _updateLast((t) => t.copyWith(streaming: false));
  }

  void _cancel() {
    _subscription?.cancel();
    _subscription = null;
    _finish();
  }

  void _finish() {
    final done = _done;
    _done = null;
    if (done != null && !done.isCompleted) done.complete();
  }

  Future<void> send(String text) async {
    final client = ref.read(activeApiClientProvider);
    final prompt = text.trim();
    if (client == null || prompt.isEmpty || state.sending || !state.ready) {
      return;
    }
    final history = [
      ..._answeredHistory(state.turns),
      LlmChatMessage(role: 'user', content: prompt),
    ];
    state = state.copyWith(
      turns: [
        ...state.turns,
        ChatTurn(role: 'user', text: prompt),
        const ChatTurn(role: 'assistant', streaming: true),
      ],
    );
    final explicit = state.accountId != null && state.model != null;
    final done = _done = Completer<void>();
    _subscription = client
        .llmChatStream(
          messages: history,
          accountId: explicit ? state.accountId : null,
          model: explicit ? state.model : null,
          purpose: explicit ? null : 'default',
        )
        .listen(
          _onEvent,
          onError: (Object error) {
            _fail(_describe(error));
            _finish();
          },
          onDone: () {
            _updateLast((t) => t.copyWith(streaming: false));
            _finish();
          },
          cancelOnError: true,
        );
    await done.future;
  }

  void _onEvent(LlmChatEvent event) {
    switch (event.type) {
      case 'start':
        _updateLast(
          (t) => t.copyWith(provider: event.provider, model: event.model),
        );
      case 'text':
        final chunk = event.text ?? '';
        if (chunk.isNotEmpty) {
          _updateLast((t) => t.copyWith(text: t.text + chunk));
        }
      case 'usage':
        _updateLast((t) => t.copyWith(totalTokens: event.totalTokens));
      case 'error':
        _fail(event.errorMessage ?? 'The model call failed');
      case 'done':
        _updateLast((t) => t.copyWith(streaming: false));
    }
  }

  void _fail(String message) {
    _updateLast((t) => t.copyWith(streaming: false, error: message));
  }

  void _updateLast(ChatTurn Function(ChatTurn) change) {
    if (state.turns.isEmpty || state.turns.last.role != 'assistant') return;
    state = state.copyWith(
      turns: [
        ...state.turns.take(state.turns.length - 1),
        change(state.turns.last),
      ],
    );
  }
}

/// Earlier exchanges that got an answer; a question whose answer failed is
/// left out so the model sees a clean user/assistant alternation.
List<LlmChatMessage> _answeredHistory(List<ChatTurn> turns) {
  final out = <LlmChatMessage>[];
  for (var i = 0; i + 1 < turns.length; i += 2) {
    final question = turns[i];
    final answer = turns[i + 1];
    if (question.role != 'user' || answer.role != 'assistant') continue;
    if (answer.failed || answer.text.isEmpty) continue;
    out
      ..add(LlmChatMessage(role: 'user', content: question.text))
      ..add(LlmChatMessage(role: 'assistant', content: answer.text));
  }
  return out;
}

String _describe(Object error) => switch (error) {
  ApiException() => error.friendly,
  ControllerUnreachableException() => error.friendly,
  _ => 'The chat stream broke off',
};

final chatProvider = NotifierProvider<ChatController, ChatState>(
  ChatController.new,
);

/// Models of one account, for the chat's model picker.
final chatModelsProvider = FutureProvider.autoDispose
    .family<List<LlmModel>, String>((ref, accountId) async {
      final client = ref.watch(activeApiClientProvider);
      if (client == null) return const [];
      return client.listLlmAccountModels(accountId);
    });
