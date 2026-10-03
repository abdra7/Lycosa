import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';
import 'openrouter_sign_in.dart';

/// Universal LLM layer state for the AI Providers screen (ADR-031).
final llmProvidersProvider = FutureProvider.autoDispose<List<LlmProvider>>((
  ref,
) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listLlmProviders();
});

final llmAccountsProvider = FutureProvider.autoDispose<List<LlmAccount>>((
  ref,
) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listLlmAccounts();
});

final llmRoutingProvider = FutureProvider.autoDispose<LlmRouting?>((ref) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return null;
  return client.getLlmRouting();
});

/// Seam: tests replace the OS browser launch.
final browserLauncherProvider = Provider<BrowserLauncher>(
  (ref) => openInSystemBrowser,
);
