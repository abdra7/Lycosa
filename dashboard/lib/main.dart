import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/brand.dart';
import 'core/session.dart';
import 'core/theme_mode.dart';
import 'features/auth/login_screen.dart';
import 'features/setup/connection_screen.dart';
import 'features/shell/shell_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // read the saved theme before the first frame so it never flashes light
  final initialTheme = await loadInitialThemeMode(SecureThemeModeStore());
  runApp(
    ProviderScope(
      overrides: [initialThemeModeProvider.overrideWithValue(initialTheme)],
      child: const LycosaApp(),
    ),
  );
}

class LycosaApp extends ConsumerWidget {
  const LycosaApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: 'Lycosa',
      theme: LycosaTheme.light(),
      darkTheme: LycosaTheme.dark(),
      // Light-first per the brand spec; the user can flip to dark from the
      // app bar and the choice is restored on next launch.
      themeMode: ref.watch(themeModeProvider),
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
