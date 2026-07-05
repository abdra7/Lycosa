import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/session.dart';
import 'features/auth/login_screen.dart';
import 'features/setup/connection_screen.dart';
import 'features/shell/shell_screen.dart';

void main() {
  runApp(const ProviderScope(child: LycosaApp()));
}

class LycosaApp extends StatelessWidget {
  const LycosaApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF4A5D8A); // muted wolf-spider blue
    return MaterialApp(
      title: 'Lycosa',
      theme: ThemeData(colorSchemeSeed: seed, brightness: Brightness.light),
      darkTheme: ThemeData(colorSchemeSeed: seed, brightness: Brightness.dark),
      themeMode: ThemeMode.system,
      home: const RootGate(),
    );
  }
}

/// Session-state-driven navigation: setup -> login -> shell.
class RootGate extends ConsumerWidget {
  const RootGate({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    return session.when(
      loading: () =>
          const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Something went wrong: $error'),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: () => ref.invalidate(sessionProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
      ),
      data: (state) {
        if (state.needsSetup) return const ConnectionScreen();
        if (!state.authenticated) return const LoginScreen();
        return const ShellScreen();
      },
    );
  }
}
