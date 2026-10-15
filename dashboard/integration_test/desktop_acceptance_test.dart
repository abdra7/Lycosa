// Live desktop acceptance (Desktop_App_Test_Cases + Sprint 14 Claude cases).
//
// Drives the real Windows app against a running, THROWAWAY controller. It
// creates and deletes resources there, so never point it at a controller
// that holds real data. See integration_test/README.md for the command.
//
// Saved sessions use in-memory stores so the run never touches the
// operator's real profiles in the OS credential store.

import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:file_picker/file_picker.dart';
// the platform seam is not re-exported; the fake replaces only the dialog
// ignore: implementation_imports
import 'package:file_picker/src/platform/file_picker_platform_interface.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';
import 'package:lycosa_dashboard/core/theme_mode.dart';
import 'package:lycosa_dashboard/main.dart';

const qaUrl = String.fromEnvironment('LYCOSA_QA_URL');
const adminEmail = String.fromEnvironment('LYCOSA_QA_EMAIL');
const adminPassword = String.fromEnvironment('LYCOSA_QA_PASSWORD');
const operatorEmail = String.fromEnvironment('LYCOSA_QA_OPERATOR_EMAIL');
const operatorPassword = String.fromEnvironment('LYCOSA_QA_OPERATOR_PASSWORD');
const outDir = String.fromEnvironment('LYCOSA_QA_OUT');
const fixturesDir = String.fromEnvironment('LYCOSA_QA_FIXTURES');
// docker container of the throwaway API, for the reconnect cases
const apiContainer = String.fromEnvironment('LYCOSA_QA_API_CONTAINER');

final profileStore = InMemoryProfileStore();
final themeStore = InMemoryThemeModeStore();
final picker = FakePicker();
final shotKey = GlobalKey();
final failures = <String>[];

/// Stands in for the native file dialog: returns a fixture file, or null
/// (the user cancelled). Everything after the dialog is the real app.
class FakePicker extends FilePickerWindows {
  String? next;

  @override
  Future<FilePickerResult?> pickFiles({
    String? dialogTitle,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    Function(FilePickerStatus)? onFileLoading,
    int compressionQuality = 0,
    bool allowMultiple = false,
    bool withData = false,
    bool withReadStream = false,
    bool lockParentWindow = false,
    bool readSequential = false,
    bool cancelUploadOnWindowBlur = true,
  }) async {
    final name = next;
    next = null;
    if (name == null) return null;
    final bytes = await File('$fixturesDir/$name').readAsBytes();
    return FilePickerResult([
      PlatformFile(name: name, size: bytes.length, bytes: bytes),
    ]);
  }
}

// --- harness -----------------------------------------------------------------

Future<void> launch(WidgetTester tester) async {
  await tester.pumpWidget(
    RepaintBoundary(
      key: shotKey,
      child: ProviderScope(
        overrides: [
          profileStoreProvider.overrideWithValue(profileStore),
          themeModeStoreProvider.overrideWithValue(themeStore),
        ],
        child: const LycosaApp(),
      ),
    ),
  );
  await idle(tester, 800);
}

Future<void> idle(WidgetTester tester, [int ms = 300]) async {
  final end = DateTime.now().add(Duration(milliseconds: ms));
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 50));
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
}

Future<void> waitFor(
  WidgetTester tester,
  Finder finder, {
  int seconds = 30,
}) async {
  final end = DateTime.now().add(Duration(seconds: seconds));
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) return;
    await Future<void>.delayed(const Duration(milliseconds: 150));
  }
  throw TestFailure('timed out after ${seconds}s waiting for $finder');
}

Future<void> waitGone(
  WidgetTester tester,
  Finder finder, {
  int seconds = 30,
}) async {
  final end = DateTime.now().add(Duration(seconds: seconds));
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isEmpty) return;
    await Future<void>.delayed(const Duration(milliseconds: 150));
  }
  throw TestFailure('timed out after ${seconds}s; still showing $finder');
}

Future<void> tap(WidgetTester tester, Finder finder, {int settle = 400}) async {
  await waitFor(tester, finder);
  await tester.ensureVisible(finder.first);
  await tester.tap(finder.first);
  await idle(tester, settle);
}

/// Types into a field. Fields with a controller get the text set directly:
/// simulated typing into an already-focused desktop field was not always
/// delivered, which a user's real keyboard never hits.
Future<void> enter(WidgetTester tester, Finder field, String text) async {
  await waitFor(tester, field);
  final widget = tester.widget(field.first);
  final controller = widget is TextField ? widget.controller : null;
  if (controller != null) {
    controller.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
  } else {
    await tester.enterText(field.first, text);
  }
  await idle(tester, 150);
}

Finder field(String label) => find.widgetWithText(TextField, label);

/// The inline result of the last task run on the Tasks screen.
final resultPanel = find.byWidgetPredicate(
  (w) => w.runtimeType.toString() == '_ResultPanel',
);

final routeMenu = find.byWidgetPredicate(
  (w) =>
      w is DropdownButtonFormField<String> && w.decoration.labelText == 'Route',
);

/// Opens a dropdown and picks [item]. Menus open scrolled to the current
/// value, so earlier or later items may not be built until scrolled to.
Future<void> choose(WidgetTester tester, Finder dropdown, String item) async {
  await tap(tester, dropdown, settle: 600);
  final option = find.text(item);
  for (final dy in [140.0, -140.0]) {
    for (var i = 0; i < 12 && option.evaluate().isEmpty; i++) {
      await tester.drag(find.byType(Scrollable).last, Offset(0, dy));
      await idle(tester, 150);
    }
  }
  await waitFor(tester, option);
  await tester.tap(option.last);
  await idle(tester, 600);
}

/// Runs the task on the Tasks screen and waits for its own result.
Future<void> runTask(WidgetTester tester, {int seconds = 180}) async {
  await tap(tester, find.text('Run task'), settle: 300);
  await waitFor(tester, find.byType(LinearProgressIndicator), seconds: 5);
  await waitGone(
    tester,
    find.byType(LinearProgressIndicator),
    seconds: seconds,
  );
}

Future<void> shot(WidgetTester tester, String name) async {
  if (outDir.isEmpty) return;
  await tester.pump();
  final boundary =
      shotKey.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await boundary.toImage();
  final data = await image.toByteData(format: ui.ImageByteFormat.png);
  await Directory('$outDir/shots').create(recursive: true);
  await File(
    '$outDir/shots/$name.png',
  ).writeAsBytes(data!.buffer.asUint8List());
}

/// One acceptance case: runs, records Passed/Failed with the reason and a
/// screenshot on failure, and never stops the remaining cases.
Future<void> tc(
  WidgetTester tester,
  String id,
  String title,
  Future<void> Function() body,
) async {
  final started = DateTime.now();
  var result = 'Passed';
  var note = '';
  try {
    await body();
    final error = tester.takeException();
    if (error != null) {
      result = 'Failed';
      note = 'framework error: $error';
    }
  } catch (e) {
    result = 'Failed';
    note = '$e'.split('\n').take(6).join(' | ');
    tester.takeException();
  }
  if (result == 'Failed') {
    failures.add('$id $title: $note');
    try {
      await shot(tester, 'FAIL-$id');
    } catch (_) {}
  }
  await record({
    'id': id,
    'title': title,
    'result': result,
    'note': note,
    'seconds': DateTime.now().difference(started).inMilliseconds / 1000,
  });
  // ignore: avoid_print
  print('[$result] $id $title${note.isEmpty ? '' : ' — $note'}');
}

Future<void> record(Map<String, Object?> entry) async {
  if (outDir.isEmpty) return;
  await Directory(outDir).create(recursive: true);
  await File(
    '$outDir/results.jsonl',
  ).writeAsString('${jsonEncode(entry)}\n', mode: FileMode.append);
}

Future<void> note(String id, String title, String result, String why) =>
    record({'id': id, 'title': title, 'result': result, 'note': why});

Future<void> nav(WidgetTester tester, String section) async {
  await tap(
    tester,
    find.descendant(
      of: find.byType(NavigationRail),
      matching: find.text(section),
    ),
    settle: 900,
  );
}

Future<void> docker(List<String> args) async {
  if (apiContainer.isEmpty) {
    throw TestFailure('LYCOSA_QA_API_CONTAINER not set');
  }
  final result = await Process.run('docker', [...args, apiContainer]);
  if (result.exitCode != 0) {
    throw TestFailure('docker ${args.join(' ')}: ${result.stderr}');
  }
}

Future<void> waitHealthy() async {
  final end = DateTime.now().add(const Duration(seconds: 120));
  while (DateTime.now().isBefore(end)) {
    try {
      final client = HttpClient();
      final request = await client.getUrl(Uri.parse('$qaUrl/healthz'));
      final response = await request.close();
      client.close();
      if (response.statusCode == 200) return;
    } catch (_) {}
    await Future<void>.delayed(const Duration(seconds: 2));
  }
  throw TestFailure('controller did not come back');
}

Future<void> signIn(WidgetTester tester, String email, String password) async {
  await enter(tester, field('Email'), email);
  await enter(tester, field('Password'), password);
  await tap(tester, find.widgetWithText(FilledButton, 'Sign in'), settle: 1500);
}

Future<void> signOut(WidgetTester tester) async {
  await tap(tester, find.byTooltip('Sign out'), settle: 1200);
  await waitFor(tester, find.widgetWithText(FilledButton, 'Sign in'));
}

// --- cases -------------------------------------------------------------------

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  final configured = qaUrl.isNotEmpty && adminPassword.isNotEmpty;

  setUpAll(() {
    FilePickerPlatform.instance = picker;
  });

  testWidgets('desktop acceptance against a throwaway controller', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    await launch(tester);

    await tc(
      tester,
      'TC-DESK-032',
      'Fresh launch shows the connection setup',
      () async {
        await waitFor(tester, field('Controller URL'));
        await shot(tester, '01-connect-empty');
      },
    );

    await tc(
      tester,
      'TC-DESK-002',
      'Invalid URL and unavailable controller',
      () async {
        await enter(tester, field('Controller URL'), 'not a url');
        await enter(tester, field('Email'), adminEmail);
        await enter(tester, field('Password'), adminPassword);
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Connect'),
          settle: 1200,
        );
        expect(find.byType(NavigationRail), findsNothing);
        await shot(tester, '02-connect-invalid-url');
        await enter(tester, field('Controller URL'), 'http://127.0.0.1:1');
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Connect'),
          settle: 4000,
        );
        expect(find.byType(NavigationRail), findsNothing);
        await shot(tester, '03-connect-unreachable');
      },
    );

    await tc(tester, 'TC-DESK-004a', 'Wrong credentials are refused', () async {
      await enter(tester, field('Controller URL'), qaUrl);
      await enter(tester, field('Password'), 'definitely-wrong-password');
      await tap(
        tester,
        find.widgetWithText(FilledButton, 'Connect'),
        settle: 2500,
      );
      expect(find.byType(NavigationRail), findsNothing);
      await shot(tester, '04-connect-wrong-password');
    });

    await tc(tester, 'TC-DESK-001', 'Valid first connection', () async {
      await enter(tester, field('Profile name (optional)'), 'QA-A');
      await enter(tester, field('Password'), adminPassword);
      final pw = tester.widget<TextField>(field('Password'));
      expect(pw.obscureText, isTrue, reason: 'password must stay obscured');
      await tap(
        tester,
        find.widgetWithText(FilledButton, 'Connect'),
        settle: 2500,
      );
      await waitFor(tester, find.byType(NavigationRail));
      expect(profileStore.profiles.single.name, 'QA-A');
      expect(profileStore.profiles.single.token, isNotNull);
      await waitFor(tester, find.text('qa-laptop'));
      await shot(tester, '05-nodes-list');
    });

    await tc(
      tester,
      'TC-DESK-003',
      'Saved session restores on relaunch',
      () async {
        await launch(tester);
        await waitFor(tester, find.byType(NavigationRail));
        expect(field('Controller URL'), findsNothing);
      },
    );

    await tc(tester, 'TC-DESK-010', 'Node list and graph navigation', () async {
      await waitFor(tester, find.text('qa-laptop'));
      await tap(tester, find.text('Graph'), settle: 1200);
      await shot(tester, '06-nodes-graph');
      await tap(tester, find.text('List'), settle: 800);
      await waitFor(tester, find.text('qa-laptop'));
    });

    await tc(
      tester,
      'TC-DESK-011',
      'CPU-only node usage and freshness',
      () async {
        await tap(tester, find.text('qa-laptop'), settle: 1500);
        await waitFor(tester, find.textContaining('CPU'));
        expect(find.text('No usage data received yet.'), findsNothing);
        await shot(tester, '07-node-detail');
      },
    );

    await tc(tester, 'TC-DESK-013', 'Role save and persistence', () async {
      await tap(
        tester,
        find.widgetWithText(DropdownButtonFormField<String>, 'Set role'),
      );
      await tap(tester, find.text('knowledge').last);
      await tap(
        tester,
        find.widgetWithText(FilledButton, 'Save'),
        settle: 1200,
      );
      await waitFor(tester, find.textContaining('Role set to'));
      await shot(tester, '08-node-role-saved');
      final back = find.byTooltip('Back');
      if (back.evaluate().isNotEmpty) await tap(tester, back, settle: 900);
    });

    await tc(
      tester,
      'TC-DESK-008',
      'Create key for a new node (one-time reveal)',
      () async {
        await nav(tester, 'Nodes');
        await tap(tester, find.text('Add node'));
        await waitFor(tester, find.text('Add a node'));
        await enter(tester, field('Key name'), 'qa-desktop-key');
        await tap(tester, find.text('Create key'), settle: 1500);
        await waitFor(
          tester,
          find.textContaining('it will not be shown again'),
        );
        await shot(tester, '09-add-node-key');
        await tester.sendKeyEvent(LogicalKeyboardKey.escape);
        await idle(tester, 600);
      },
    );

    await tc(tester, 'TC-DESK-009', 'LAN discovery scan', () async {
      await tap(tester, find.text('Scan'), settle: 6000);
      await shot(tester, '10-lan-scan');
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await idle(tester, 600);
    });

    // --- Sprint 14: Claude through the owner's plan -------------------------

    await tc(
      tester,
      'S14-CLAUDE-01',
      'Connect the Claude plan from Providers',
      () async {
        await nav(tester, 'Providers');
        await waitFor(tester, find.text('Claude subscription'));
        await shot(tester, '11-providers');
        final card = find.ancestor(
          of: find.text('Claude subscription'),
          matching: find.byType(Card),
        );
        final connect = find.descendant(
          of: card.first,
          matching: find.byWidgetPredicate((w) => w is TextButton),
        );
        await tap(tester, connect, settle: 800);
        await waitFor(tester, find.text('Connect your Claude plan'));
        await waitFor(tester, find.textContaining('Signed in'), seconds: 60);
        await shot(tester, '12-claude-connect-dialog');
        await enter(tester, field('Name'), 'Desk Claude');
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Connect'),
          settle: 1500,
        );
        await waitFor(tester, find.text('Desk Claude'));
        expect(find.textContaining('Runs on qa-laptop'), findsWidgets);
      },
    );

    await tc(tester, 'S14-CLAUDE-02', 'Test the plan connection', () async {
      final row = find.ancestor(
        of: find.text('Desk Claude'),
        matching: find.byType(ListTile),
      );
      await tap(
        tester,
        find.descendant(of: row, matching: find.text('Test')),
        settle: 1000,
      );
      await waitFor(tester, find.textContaining('signed in'), seconds: 60);
      await shot(tester, '13-claude-test-ready');
      await tap(tester, find.text('Close'));
    });

    await tc(
      tester,
      'S14-CHAT-01',
      'Chat streams a real Claude answer',
      () async {
        await nav(tester, 'Chat');
        await choose(
          tester,
          find.byType(DropdownButton<String?>),
          'Claude subscription · Desk Claude',
        );
        await waitFor(tester, find.text('Plan default'), seconds: 30);
        await choose(
          tester,
          find.text('Plan default'),
          'Claude Haiku (latest)',
        );
        await enter(
          tester,
          find.byKey(const Key('chat-input')),
          'Reply with exactly: LYCOSA-CHAT-OK',
        );
        await tap(tester, find.text('Send'), settle: 500);
        await waitFor(
          tester,
          find.textContaining('LYCOSA-CHAT-OK'),
          seconds: 120,
        );
        await waitFor(tester, find.textContaining('tokens'), seconds: 60);
        await shot(tester, '14-chat-answer');
      },
    );

    await tc(
      tester,
      'S14-CHAT-02',
      'Chat keeps context across turns and survives navigation',
      () async {
        await enter(
          tester,
          find.byKey(const Key('chat-input')),
          'What did I ask you to reply with? Quote it.',
        );
        await tap(tester, find.text('Send'), settle: 500);
        await waitFor(
          tester,
          find.textContaining('LYCOSA-CHAT-OK'),
          seconds: 10,
        );
        await waitFor(tester, find.text('Send'), seconds: 120);
        expect(find.textContaining('LYCOSA-CHAT-OK'), findsAtLeastNWidgets(3));
        await nav(tester, 'Tasks');
        await nav(tester, 'Chat');
        expect(find.textContaining('LYCOSA-CHAT-OK'), findsAtLeastNWidgets(3));
        await shot(tester, '15-chat-multiturn');
        await tap(tester, find.byTooltip('New chat'));
      },
    );

    await tc(tester, 'S14-TASK-01', 'Task runs on the Claude plan', () async {
      await nav(tester, 'Tasks');
      await choose(
        tester,
        routeMenu,
        'Account: Claude subscription · Desk Claude',
      );
      await enter(tester, field('Model (optional)'), 'haiku');
      await enter(
        tester,
        field('Prompt'),
        'What is 2 + 2? Answer with the number only.',
      );
      await runTask(tester);
      expect(
        find.descendant(
          of: resultPanel,
          matching: find.textContaining('succeeded'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(of: resultPanel, matching: find.textContaining('4')),
        findsWidgets,
      );
      await shot(tester, '16-task-claude-result');
    });

    await tc(tester, 'TC-DESK-017', 'Blank prompt is not submitted', () async {
      await enter(tester, field('Prompt'), '   ');
      final before = find.textContaining('succeeded').evaluate().length;
      await tap(tester, find.text('Run task'), settle: 1500);
      expect(find.byType(LinearProgressIndicator), findsNothing);
      expect(find.textContaining('succeeded').evaluate().length, before);
    });

    await tc(
      tester,
      'TC-DESK-018',
      'No eligible node gives a clear error',
      () async {
        await choose(tester, routeMenu, 'Provider above (node / legacy route)');
        await enter(tester, field('Model (optional)'), 'llama3.2:1b');
        await enter(tester, field('Prompt'), 'hello');
        await runTask(tester, seconds: 60);
        expect(
          find.descendant(
            of: resultPanel,
            matching: find.textContaining('No compatible online node'),
          ),
          findsWidgets,
        );
        await shot(tester, '17-task-no-node');
      },
    );

    await note(
      'TC-DESK-016',
      'Local task happy path',
      'Blocked',
      'no local model by owner decision (Claude-only run); S14-TASK-01 covers the task flow on the Claude plan',
    );

    await tc(tester, 'TC-DESK-020', 'Create and approve a workflow', () async {
      await nav(tester, 'Workflows');
      await tap(tester, find.text('New workflow'));
      await enter(tester, field('Name'), 'qa-desktop-ui-flow');
      await enter(
        tester,
        field('Definition (JSON)'),
        jsonEncode({
          'steps': [
            {
              'id': 'plan',
              'kind': 'task',
              'route': 'default',
              'prompt': 'In one short sentence, plan how to: {{input}}',
            },
            {'id': 'gate', 'kind': 'approval', 'message': 'Review the plan'},
            {
              'id': 'do',
              'kind': 'task',
              'route': 'default',
              'prompt':
                  'In one sentence, explain this plan: {{steps.plan.output}}',
            },
          ],
        }),
      );
      await tap(
        tester,
        find.widgetWithText(FilledButton, 'Create'),
        settle: 1200,
      );
      await waitFor(tester, find.text('qa-desktop-ui-flow'));
      await shot(tester, '18-workflows');
      final row = find.ancestor(
        of: find.text('qa-desktop-ui-flow'),
        matching: find.byType(ListTile),
      );
      await tap(tester, find.descendant(of: row, matching: find.text('Run')));
      await enter(tester, field('Input'), 'water the office plants');
      await tap(
        tester,
        find.widgetWithText(FilledButton, 'Run').last,
        settle: 1500,
      );
      await waitFor(
        tester,
        find.text('This run is paused waiting for your approval.'),
        seconds: 180,
      );
      await shot(tester, '19-workflow-paused');
      await tap(tester, find.text('Approve'), settle: 1500);
      await waitFor(
        tester,
        find.textContaining('do · succeeded'),
        seconds: 180,
      );
      await shot(tester, '20-workflow-succeeded');
      final back = find.byTooltip('Back');
      if (back.evaluate().isNotEmpty) await tap(tester, back);
    });

    await tc(
      tester,
      'TC-DESK-021',
      'Reject a run; invalid definition refused',
      () async {
        await nav(tester, 'Workflows');
        final row = find.ancestor(
          of: find.text('qa-desktop-ui-flow'),
          matching: find.byType(ListTile),
        );
        await tap(tester, find.descendant(of: row, matching: find.text('Run')));
        await enter(tester, field('Input'), 'tidy the desk');
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Run').last,
          settle: 1500,
        );
        await waitFor(tester, find.text('Reject'), seconds: 180);
        await tap(tester, find.text('Reject'), settle: 1500);
        await waitFor(tester, find.textContaining('failed'), seconds: 60);
        await shot(tester, '21-workflow-rejected');
        final back = find.byTooltip('Back');
        if (back.evaluate().isNotEmpty) await tap(tester, back);
        await nav(tester, 'Workflows');
        await tap(tester, find.text('New workflow'));
        await enter(tester, field('Name'), 'qa-desktop-bad');
        await enter(
          tester,
          field('Definition (JSON)'),
          '{"steps": [{"id": "a", "kind": "task", "prompt": "{{steps.zzz.output}}"}]}',
        );
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Create'),
          settle: 1200,
        );
        expect(
          find.text('qa-desktop-bad'),
          findsNothing,
          reason: 'invalid workflow must not be saved',
        );
        await shot(tester, '22-workflow-invalid');
        await tap(tester, find.text('Cancel'));
      },
    );

    await tc(
      tester,
      'TC-DESK-022',
      'Create a collection and ingest a document',
      () async {
        await nav(tester, 'Knowledge');
        await tap(tester, find.byTooltip('New collection'));
        await enter(tester, field('Name'), 'qa-desktop-ui');
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Create'),
          settle: 1200,
        );
        await tap(tester, find.text('qa-desktop-ui'), settle: 800);
        picker.next = 'qa-policy.txt';
        await tap(tester, find.text('Upload document'), settle: 1500);
        await waitFor(tester, find.textContaining('embedded'), seconds: 60);
        await shot(tester, '23-knowledge-ingested');
      },
    );

    await tc(
      tester,
      'TC-DESK-023',
      'Picker cancel and a failed ingest',
      () async {
        picker.next = null;
        await tap(tester, find.text('Upload document'), settle: 1200);
        picker.next = 'scan.pdf';
        await tap(tester, find.text('Upload document'), settle: 1500);
        await waitFor(tester, find.textContaining('scanned'), seconds: 60);
        await shot(tester, '24-knowledge-scan-refused');
      },
    );

    await tc(tester, 'TC-DESK-024', 'Scoped retrieval', () async {
      await enter(tester, field('Query'), 'desktop test support code');
      await tap(tester, find.text('Retrieve'), settle: 1500);
      await waitFor(tester, find.textContaining('QA-LYCOSA-731'), seconds: 30);
      await shot(tester, '25-knowledge-retrieve');
    });

    await tc(
      tester,
      'TC-DESK-025',
      'Knowledge-enhanced task (Claude route)',
      () async {
        await nav(tester, 'Tasks');
        await choose(
          tester,
          routeMenu,
          'Account: Claude subscription · Desk Claude',
        );
        await enter(tester, field('Model (optional)'), 'haiku');
        await enter(
          tester,
          field('Prompt'),
          'What is the Lycosa desktop test support code? Answer with the code only.',
        );
        await enter(
          tester,
          field('Knowledge query (optional)'),
          'desktop test support code',
        );
        await enter(
          tester,
          find.byWidgetPredicate(
            (w) =>
                w is TextField &&
                (w.decoration?.labelText ?? '').startsWith(
                  'Knowledge collection',
                ),
          ),
          'qa-desktop-ui',
        );
        await runTask(tester);
        expect(
          find.descendant(
            of: resultPanel,
            matching: find.textContaining('QA-LYCOSA-731'),
          ),
          findsWidgets,
        );
        await shot(tester, '26-task-grounded');
      },
    );

    await tc(
      tester,
      'TC-DESK-026',
      'Cancel, then confirm collection deletion',
      () async {
        await nav(tester, 'Knowledge');
        final tile = find.ancestor(
          of: find.text('qa-desktop-ui'),
          matching: find.byType(ListTile),
        );
        await tap(
          tester,
          find.descendant(
            of: tile,
            matching: find.byTooltip('Delete collection'),
          ),
        );
        await waitFor(tester, find.text('Delete collection?'));
        await tap(tester, find.text('Cancel'));
        expect(find.text('qa-desktop-ui'), findsWidgets);
        await tap(
          tester,
          find.descendant(
            of: tile,
            matching: find.byTooltip('Delete collection'),
          ),
        );
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Delete'),
          settle: 1500,
        );
        await waitGone(tester, find.text('qa-desktop-ui'));
      },
    );

    await tc(
      tester,
      'TC-DESK-029',
      'Revoke the test key and inspect the audit log',
      () async {
        await nav(tester, 'Admin');
        await waitFor(tester, find.textContaining('qa-desktop-key'));
        final keyRow = find.ancestor(
          of: find.textContaining('qa-desktop-key'),
          matching: find.byType(ListTile),
        );
        await tap(
          tester,
          find.descendant(of: keyRow, matching: find.text('Revoke')),
          settle: 800,
        );
        final confirm = find.widgetWithText(FilledButton, 'Revoke');
        if (confirm.evaluate().isNotEmpty) {
          await tap(tester, confirm, settle: 1200);
        }
        await waitFor(tester, find.textContaining('Revoked'), seconds: 20);
        await waitFor(tester, find.text('apikey.revoke'), seconds: 20);
        await shot(tester, '27-admin-audit');
      },
    );

    await tc(tester, 'TC-DESK-030', 'Dark theme and a narrow window', () async {
      await tap(tester, find.byTooltip('Switch to dark mode'), settle: 800);
      expect(themeStore.value, 'dark');
      await nav(tester, 'Chat');
      await shot(tester, '28-dark-chat');
      await nav(tester, 'Providers');
      await shot(tester, '29-dark-providers');
      await nav(tester, 'Nodes');
      await shot(tester, '30-dark-nodes');
      tester.view.physicalSize = const Size(900, 640);
      await idle(tester, 800);
      for (final section in [
        'Nodes',
        'Tasks',
        'Chat',
        'Knowledge',
        'Providers',
      ]) {
        await nav(tester, section);
        await shot(tester, '31-narrow-${section.toLowerCase()}');
      }
      tester.view.physicalSize = const Size(1440, 900);
      await tap(tester, find.byTooltip('Switch to light mode'), settle: 800);
    });

    await tc(
      tester,
      'TC-DESK-031',
      'Live events and reconnect after a controller restart',
      () async {
        await waitFor(tester, find.text('live'), seconds: 30);
        await docker(['restart']);
        await waitFor(tester, find.text('connecting…'), seconds: 60);
        await shot(tester, '32-events-reconnecting');
        await waitHealthy();
        await waitFor(tester, find.text('live'), seconds: 90);
      },
    );

    await tc(tester, 'TC-DESK-019', 'Connection lost during a task', () async {
      await nav(tester, 'Tasks');
      await choose(
        tester,
        routeMenu,
        'Account: Claude subscription · Desk Claude',
      );
      await enter(tester, field('Model (optional)'), 'haiku');
      await enter(
        tester,
        field('Prompt'),
        'Write a 300-word story about a lighthouse keeper.',
      );
      await tap(tester, find.text('Run task'), settle: 2500);
      await docker(['restart']);
      await waitFor(
        tester,
        find.textContaining('may still be running'),
        seconds: 120,
      );
      await shot(tester, '33-task-connection-lost');
      await waitHealthy();
      await waitFor(tester, find.text('live'), seconds: 90);
    });

    await tc(
      tester,
      'TC-DESK-028',
      'Operator cannot open admin or use the admin plan',
      () async {
        await signOut(tester);
        await signIn(tester, operatorEmail, operatorPassword);
        await waitFor(tester, find.byType(NavigationRail));
        await nav(tester, 'Admin');
        await waitFor(
          tester,
          find.text('This section requires the admin role.'),
        );
        await shot(tester, '34-operator-admin-denied');
        await nav(tester, 'Chat');
        expect(find.textContaining('Desk Claude'), findsNothing);
        await nav(tester, 'Providers');
        await waitFor(tester, find.text('Claude subscription'));
        await shot(tester, '35-operator-providers');
      },
    );

    await tc(
      tester,
      'TC-DESK-006',
      'Online logout returns to sign-in',
      () async {
        await signOut(tester);
        expect(field('Password'), findsOneWidget);
        await signIn(tester, adminEmail, adminPassword);
        await waitFor(tester, find.byType(NavigationRail));
      },
    );

    await tc(
      tester,
      'TC-DESK-027',
      'Cancel, then confirm node deletion; the plan reports the node gone',
      () async {
        await nav(tester, 'Nodes');
        await tap(tester, find.text('qa-laptop'), settle: 1500);
        await tap(tester, find.text('Delete'));
        await waitFor(tester, find.text('Delete qa-laptop?'));
        await tap(tester, find.text('Cancel'));
        await tap(tester, find.text('Delete'));
        await tap(
          tester,
          find.widgetWithText(FilledButton, 'Delete'),
          settle: 1500,
        );
        await waitFor(tester, find.textContaining('qa-laptop deleted'));
        await nav(tester, 'Chat');
        await choose(
          tester,
          find.byType(DropdownButton<String?>),
          'Claude subscription · Desk Claude',
        );
        await idle(tester, 1500);
        await enter(tester, find.byKey(const Key('chat-input')), 'hello');
        await tap(tester, find.text('Send'), settle: 500);
        await waitFor(
          tester,
          find.textContaining('not available'),
          seconds: 60,
        );
        await shot(tester, '36-chat-node-gone');
      },
    );

    await note(
      'TC-DESK-005',
      'Profile isolation',
      'Blocked',
      'needs a second throwaway controller',
    );
    await note(
      'TC-DESK-007',
      'Offline logout limitation',
      'Not Run',
      'covered by unit test login_flow; not repeated live',
    );
    await note(
      'TC-DESK-012',
      'GPU/VRAM values',
      'Blocked',
      'Intel UHD only: no supported GPU collector on this PC',
    );
    await note(
      'TC-DESK-014',
      'Recommended model installation',
      'Blocked',
      'no Ollama by owner decision',
    );
    await note(
      'TC-DESK-015',
      'Offline model installation',
      'Blocked',
      'no Ollama by owner decision',
    );

    expect(failures, isEmpty, reason: failures.join('\n'));
  }, skip: !configured);
}
