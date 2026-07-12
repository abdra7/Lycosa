import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/core/app_info.dart';

// Guards against the in-app version constant drifting from pubspec.yaml — the
// bug that shipped v0.3.0 installers still displaying "v0.2.1". Tests run with
// the package root as the working directory, so pubspec.yaml is readable here.
void main() {
  test('appVersion matches pubspec.yaml version (before the +build)', () {
    final line = File('pubspec.yaml')
        .readAsLinesSync()
        .firstWhere((l) => l.startsWith('version:'));
    final pubspecVersion = line.split(':')[1].trim().split('+').first;

    expect(
      appVersion,
      pubspecVersion,
      reason:
          'lib/core/app_info.dart appVersion ($appVersion) must equal '
          'pubspec.yaml version ($pubspecVersion) — bump both on release.',
    );
  });
}
