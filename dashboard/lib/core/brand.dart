import 'package:flutter/material.dart';

/// Lycosa brand identity: the single source of truth for colors, status
/// semantics, and the application theme.
///
/// Primary accent is #A8C7FA everywhere; neutrals adapt per brightness via
/// [LycosaPalette]. Status colors are fixed tokens (not theme-derived) so a
/// "running" green or "error" red reads the same on every screen.
abstract final class LycosaColors {
  // Brand
  static const primary = Color(0xFFA8C7FA);
  static const primaryHover = Color(0xFF95B8F8);
  static const primaryLight = Color(0xFFEAF3FF);

  /// Ink used on top of the light primary — deep navy for AA contrast.
  static const onPrimary = Color(0xFF13294B);

  // Surfaces (light defaults — prefer Theme.of(context).colorScheme in
  // widgets so dark mode picks up the right values).
  static const background = Color(0xFFFFFFFF);
  static const backgroundSecondary = Color(0xFFF8FAFC);
  static const border = Color(0xFFE5E7EB);

  // Text
  static const textPrimary = Color(0xFF111827);
  static const textSecondary = Color(0xFF6B7280);

  // Status
  static const success = Color(0xFF22C55E);
  static const warning = Color(0xFFF59E0B);
  static const error = Color(0xFFEF4444);
  static const loading = primary;

  /// Shared status → color mapping used by chips, banners, and step tiles.
  static Color status(String status) => switch (status) {
    'online' || 'succeeded' || 'embedded' || 'running' => success,
    'offline' || 'failed' || 'error' => error,
    'skipped' => textSecondary,
    _ => warning, // registered / pending / paused / pending_approval …
  };
}

/// Brightness-dependent neutrals. The brand accent (#A8C7FA) is shared;
/// surfaces, borders, and ink flip between the light and dark palettes.
class LycosaPalette {
  const LycosaPalette({
    required this.primary,
    required this.primaryHover,
    required this.primaryLight,
    required this.onPrimary,
    required this.background,
    required this.backgroundSecondary,
    required this.border,
    required this.textPrimary,
    required this.textSecondary,
  });

  final Color primary;
  final Color primaryHover;
  final Color primaryLight; // subtle accent container
  final Color onPrimary;
  final Color background;
  final Color backgroundSecondary;
  final Color border;
  final Color textPrimary;
  final Color textSecondary;

  static const light = LycosaPalette(
    primary: LycosaColors.primary,
    primaryHover: LycosaColors.primaryHover,
    primaryLight: LycosaColors.primaryLight,
    onPrimary: LycosaColors.onPrimary,
    background: LycosaColors.background,
    backgroundSecondary: LycosaColors.backgroundSecondary,
    border: LycosaColors.border,
    textPrimary: LycosaColors.textPrimary,
    textSecondary: LycosaColors.textSecondary,
  );

  static const dark = LycosaPalette(
    primary: LycosaColors.primary,
    primaryHover: Color(0xFFBBD3FB), // hover lightens on dark surfaces
    primaryLight: Color(0xFF22304A), // navy accent container
    onPrimary: LycosaColors.onPrimary,
    background: Color(0xFF0F1522),
    backgroundSecondary: Color(0xFF161E30),
    border: Color(0xFF283349),
    textPrimary: Color(0xFFE6EAF2),
    textSecondary: Color(0xFF97A1B3),
  );
}

/// Motion tokens — smooth, quick transitions (150–250ms) across the app.
abstract final class LycosaMotion {
  static const fast = Duration(milliseconds: 150);
  static const base = Duration(milliseconds: 200);
  static const slow = Duration(milliseconds: 250);
  static const curve = Curves.easeOutCubic;
}

abstract final class LycosaTheme {
  static ThemeData light() => _theme(LycosaPalette.light, Brightness.light);

  static ThemeData dark() => _theme(LycosaPalette.dark, Brightness.dark);

  static ThemeData _theme(LycosaPalette p, Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    // Ink used on inverse surfaces (snackbars, tooltips).
    final onInverse = isDark ? const Color(0xFF0F1522) : Colors.white;

    final scheme =
        ColorScheme.fromSeed(
          seedColor: p.primary,
          brightness: brightness,
        ).copyWith(
          primary: p.primary,
          onPrimary: p.onPrimary,
          primaryContainer: p.primaryLight,
          onPrimaryContainer: isDark ? p.textPrimary : p.onPrimary,
          secondary: p.primaryHover,
          onSecondary: p.onPrimary,
          secondaryContainer: p.primaryLight,
          onSecondaryContainer: isDark ? p.textPrimary : p.onPrimary,
          surface: p.background,
          onSurface: p.textPrimary,
          onSurfaceVariant: p.textSecondary,
          surfaceContainerLowest: p.background,
          surfaceContainerLow: p.backgroundSecondary,
          surfaceContainer: p.backgroundSecondary,
          surfaceContainerHigh: p.backgroundSecondary,
          surfaceContainerHighest: p.backgroundSecondary,
          outline: p.border,
          outlineVariant: p.border,
          error: LycosaColors.error,
          onError: Colors.white,
          surfaceTint: Colors.transparent,
        );

    final base = ThemeData(colorScheme: scheme, brightness: brightness);

    final textTheme = base.textTheme.apply(
      bodyColor: p.textPrimary,
      displayColor: p.textPrimary,
    );

    OutlineInputBorder inputBorder(Color color, [double width = 1]) =>
        OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: color, width: width),
        );

    return base.copyWith(
      scaffoldBackgroundColor: p.background,
      canvasColor: p.background,
      dividerColor: p.border,
      hoverColor: p.backgroundSecondary,
      focusColor: p.primaryLight,
      splashColor: p.primaryLight.withValues(alpha: 0.4),
      highlightColor: p.primaryLight.withValues(alpha: 0.4),
      textTheme: textTheme.copyWith(
        headlineSmall: textTheme.headlineSmall?.copyWith(
          fontWeight: FontWeight.w700,
        ),
        titleLarge: textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        titleMedium: textTheme.titleMedium?.copyWith(
          fontWeight: FontWeight.w600,
        ),
        bodySmall: textTheme.bodySmall?.copyWith(color: p.textSecondary),
        labelSmall: textTheme.labelSmall?.copyWith(color: p.textSecondary),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: p.background,
        foregroundColor: p.textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        centerTitle: false,
        titleSpacing: 20,
        shape: Border(bottom: BorderSide(color: p.border, width: 1)),
        titleTextStyle: textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          color: p.textPrimary,
        ),
      ),
      cardTheme: CardThemeData(
        color: p.background,
        elevation: 1,
        shadowColor: Colors.black.withValues(alpha: isDark ? 0.3 : 0.06),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: p.border),
        ),
        margin: const EdgeInsets.symmetric(vertical: 6),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.disabled)) {
              return p.border;
            }
            if (states.contains(WidgetState.hovered) ||
                states.contains(WidgetState.pressed)) {
              return p.primaryHover;
            }
            return p.primary;
          }),
          foregroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.disabled)
                ? p.textSecondary
                : p.onPrimary,
          ),
          overlayColor: const WidgetStatePropertyAll(Colors.transparent),
          elevation: const WidgetStatePropertyAll(0),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
          padding: const WidgetStatePropertyAll(
            EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          ),
          textStyle: WidgetStatePropertyAll(
            textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w600),
          ),
          animationDuration: LycosaMotion.base,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.hovered)
                ? p.backgroundSecondary
                : p.background,
          ),
          foregroundColor: WidgetStatePropertyAll(p.textPrimary),
          side: WidgetStatePropertyAll(BorderSide(color: p.border)),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
          padding: const WidgetStatePropertyAll(
            EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          ),
          textStyle: WidgetStatePropertyAll(
            textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w600),
          ),
          animationDuration: LycosaMotion.base,
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: ButtonStyle(
          foregroundColor: WidgetStatePropertyAll(p.textPrimary),
          overlayColor: WidgetStatePropertyAll(
            p.primaryLight.withValues(alpha: 0.5),
          ),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          ),
          textStyle: WidgetStatePropertyAll(
            textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w600),
          ),
          animationDuration: LycosaMotion.base,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: ButtonStyle(
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          ),
          animationDuration: LycosaMotion.base,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: p.backgroundSecondary,
        hoverColor: p.backgroundSecondary,
        border: inputBorder(p.border),
        enabledBorder: inputBorder(p.border),
        focusedBorder: inputBorder(p.primary, 2),
        errorBorder: inputBorder(LycosaColors.error),
        focusedErrorBorder: inputBorder(LycosaColors.error, 2),
        labelStyle: TextStyle(color: p.textSecondary),
        hintStyle: TextStyle(color: p.textSecondary),
        isDense: true,
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: p.background,
        indicatorColor: p.primary,
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
        selectedIconTheme: IconThemeData(color: p.onPrimary, size: 22),
        unselectedIconTheme: IconThemeData(color: p.textSecondary, size: 22),
        selectedLabelTextStyle: textTheme.labelMedium!.copyWith(
          color: p.textPrimary,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelTextStyle: textTheme.labelMedium!.copyWith(
          color: p.textSecondary,
        ),
        useIndicator: true,
        minWidth: 76,
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        selectedTileColor: p.primaryLight,
        selectedColor: p.textPrimary,
        iconColor: p.textSecondary,
      ),
      chipTheme: ChipThemeData(
        backgroundColor: p.background,
        side: BorderSide(color: p.border),
        shape: const StadiumBorder(),
        labelStyle: textTheme.labelMedium?.copyWith(color: p.textPrimary),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: p.background,
        surfaceTintColor: Colors.transparent,
        elevation: 12,
        shadowColor: Colors.black.withValues(alpha: isDark ? 0.5 : 0.15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: p.border),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          fontSize: 20,
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: p.textPrimary,
        contentTextStyle: TextStyle(color: onInverse),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        elevation: 4,
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: p.textPrimary,
          borderRadius: BorderRadius.circular(8),
        ),
        textStyle: TextStyle(color: onInverse, fontSize: 12),
        waitDuration: const Duration(milliseconds: 400),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: LycosaColors.loading,
        linearTrackColor: p.primaryLight,
        circularTrackColor: p.primaryLight,
      ),
      dividerTheme: DividerThemeData(color: p.border, thickness: 1, space: 1),
      dataTableTheme: DataTableThemeData(
        headingTextStyle: textTheme.labelMedium?.copyWith(
          color: p.textSecondary,
          fontWeight: FontWeight.w600,
        ),
        headingRowColor: WidgetStatePropertyAll(p.backgroundSecondary),
        dataRowColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.hovered)
              ? p.backgroundSecondary
              : Colors.transparent,
        ),
        dividerThickness: 1,
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        menuStyle: MenuStyle(
          backgroundColor: WidgetStatePropertyAll(p.background),
          surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: p.border),
            ),
          ),
        ),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: p.background,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: p.border),
        ),
        elevation: 6,
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? p.primary
              : Colors.transparent,
        ),
        checkColor: WidgetStatePropertyAll(p.onPrimary),
        side: BorderSide(color: p.border, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(5)),
      ),
      expansionTileTheme: ExpansionTileThemeData(
        shape: const RoundedRectangleBorder(side: BorderSide.none),
        collapsedShape: const RoundedRectangleBorder(side: BorderSide.none),
        iconColor: p.textSecondary,
        collapsedIconColor: p.textSecondary,
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.selected)
                ? p.primaryLight
                : p.background,
          ),
          foregroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.selected)
                ? (isDark ? p.textPrimary : p.onPrimary)
                : p.textSecondary,
          ),
          side: WidgetStatePropertyAll(BorderSide(color: p.border)),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
          textStyle: WidgetStatePropertyAll(
            textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
          visualDensity: VisualDensity.compact,
          animationDuration: LycosaMotion.base,
        ),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: p.textPrimary,
        unselectedLabelColor: p.textSecondary,
        indicatorColor: p.primary,
        dividerColor: p.border,
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStatePropertyAll(
          p.textSecondary.withValues(alpha: 0.35),
        ),
        radius: const Radius.circular(8),
      ),
    );
  }
}
