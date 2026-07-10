import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';

/// Poll interval — overridable so the Sprint 9 WebSocket (or tests) can
/// replace the timer without touching consumers.
final nodePollIntervalProvider = Provider<Duration>(
  (ref) => const Duration(seconds: 10),
);

/// Faster cadence for the single-node detail screen: a full node list is
/// expensive to poll often, but one node's metrics are cheap, and this is
/// the screen the user is actively watching for a task-manager-like feel.
/// Matches the controller's default agent heartbeat interval (5s) — polling
/// faster than the agent actually reports wouldn't show anything new.
final nodeDetailPollIntervalProvider = Provider<Duration>(
  (ref) => const Duration(seconds: 5),
);

/// Node inventory, refreshed on an interval while watched. The timer is
/// cancelled on dispose (no leaked timers in tests or on tab switch).
final nodesProvider = StreamProvider.autoDispose<List<NodeInfo>>((ref) {
  final client = ref.watch(activeApiClientProvider);
  final controller = StreamController<List<NodeInfo>>();
  if (client == null) {
    controller.add(const []);
    ref.onDispose(controller.close);
    return controller.stream;
  }

  Future<void> tick() async {
    try {
      final nodes = await client.listNodes();
      if (!controller.isClosed) controller.add(nodes);
    } catch (error, stack) {
      if (!controller.isClosed) controller.addError(error, stack);
    }
  }

  tick();
  final timer = Timer.periodic(
    ref.watch(nodePollIntervalProvider),
    (_) => tick(),
  );
  ref.onDispose(() {
    timer.cancel();
    controller.close();
  });
  return controller.stream;
});

/// Hardware-fit LLM recommendations for one node. Fetched once per screen
/// visit; invalidated after an install so the "installed" badges refresh.
final llmRecommendationsProvider = FutureProvider.autoDispose
    .family<List<LlmRecommendationInfo>, String>((ref, id) async {
      final client = ref.watch(activeApiClientProvider);
      if (client == null) throw StateError('not authenticated');
      return client.getLlmRecommendations(id);
    });

/// Single-node detail, polled fast (see [nodeDetailPollIntervalProvider]) so
/// "Latest metrics" on the detail screen actually stays latest instead of
/// freezing at whatever the screen showed when it first opened.
final nodeDetailProvider = StreamProvider.autoDispose.family<NodeInfo, String>((
  ref,
  id,
) {
  final client = ref.watch(activeApiClientProvider);
  final controller = StreamController<NodeInfo>();
  if (client == null) {
    controller.addError(StateError('not authenticated'));
    ref.onDispose(controller.close);
    return controller.stream;
  }

  Future<void> tick() async {
    try {
      final node = await client.getNode(id);
      if (!controller.isClosed) controller.add(node);
    } catch (error, stack) {
      if (!controller.isClosed) controller.addError(error, stack);
    }
  }

  tick();
  final timer = Timer.periodic(
    ref.watch(nodeDetailPollIntervalProvider),
    (_) => tick(),
  );
  ref.onDispose(() {
    timer.cancel();
    controller.close();
  });
  return controller.stream;
});
