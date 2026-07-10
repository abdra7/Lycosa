import 'package:flutter/material.dart';

/// Lycosa brand identity: the single source of truth for colors, status
/// semantics, and the application theme.
///
/// Primary accent is #A8C7FA everywhere; neutrals stay white/gray for
/// readability. Status colors are fixed tokens (not theme-derived) so a
/// "running" green or "error" red reads the same on every screen.
abstract final class LycosaColors {
  // Brand
  static const primary = Color(0xFFA8C7FA);
  static const primaryHover = Color(0xFF95B8F8);
  static const primaryLight = Color(0xFFEAF3FF);

  /// Ink used on top of the light primary — deep navy for AA contrast.
  static const onPrimary = Color(0xFF13294B);

  // Surfaces
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

/// Motion tokens — smooth, quick transitions (150–250ms) across the app.
abstract final class LycosaMotion {
  static const fast = Duration(milliseconds: 150);
  static const base = Duration(milliseconds: 200);
  static const slow = Duration(milliseconds: 250);
  static const curve = Curves.easeOutCubic;
}

abstract final class LycosaTheme {
  static ThemeData light() {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: LycosaColors.primary,
          brightness: Brightness.light,
        ).copyWith(
          primary: LycosaColors.primary,
          onPrimary: LycosaColors.onPrimary,
          primaryContainer: LycosaColors.primaryLight,
          onPrimaryContainer: LycosaColors.onPrimary,
          secondary: LycosaColors.primaryHover,
          onSecondary: LycosaColors.onPrimary,
          secondaryContainer: LycosaColors.primaryLight,
          onSecondaryContainer: LycosaColors.onPrimary,
          surface: LycosaColors.background,
          onSurface: LycosaColors.textPrimary,
          onSurfaceVariant: LycosaColors.textSecondary,
          surfaceContainerLowest: LycosaColors.background,
          surfaceContainerLow: LycosaColors.backgroundSecondary,
          surfaceContainer: LycosaColors.backgroundSecondary,
          surfaceContainerHigh: LycosaColors.backgroundSecondary,
          surfaceContainerHighest: LycosaColors.backgroundSecondary,
          outline: LycosaColors.border,
          outlineVariant: LycosaColors.border,
          error: LycosaColors.error,
          onError: Colors.white,
          surfaceTint: Colors.transparent,
        );

    final base = ThemeData(colorScheme: scheme, brightness: Brightness.light);

    final textTheme = base.textTheme.apply(
      bodyColor: LycosaColors.textPrimary,
      displayColor: LycosaColors.textPrimary,
    );

    OutlineInputBorder inputBorder(Color color, [double width = 1]) =>
        OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: color, width: width),
        );

    return base.copyWith(
      scaffoldBackgroundColor: LycosaColors.background,
      canvasColor: LycosaColors.background,
      dividerColor: LycosaColors.border,
      hoverColor: LycosaColors.backgroundSecondary,
      focusColor: LycosaColors.primaryLight,
      splashColor: LycosaColors.primaryLight.withValues(alpha: 0.4),
      highlightColor: LycosaColors.primaryLight.withValues(alpha: 0.4),
      textTheme: textTheme.copyWith(
        headlineSmall: textTheme.headlineSmall?.copyWith(
          fontWeight: FontWeight.w700,
        ),
        titleLarge: textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        titleMedium: textTheme.titleMedium?.copyWith(
          fontWeight: FontWeight.w600,
        ),
        bodySmall: textTheme.bodySmall?.copyWith(
          color: LycosaColors.textSecondary,
        ),
        labelSmall: textTheme.labelSmall?.copyWith(
          color: LycosaColors.textSecondary,
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: LycosaColors.background,
        foregroundColor: LycosaColors.textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        centerTitle: false,
        titleSpacing: 20,
        shape: const Border(
          bottom: BorderSide(color: LycosaColors.border, width: 1),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          color: LycosaColors.textPrimary,
        ),
      ),
      cardTheme: CardThemeData(
        color: LycosaColors.background,
        elevation: 1,
        shadowColor: Colors.black.withValues(alpha: 0.06),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: const BorderSide(color: LycosaColors.border),
        ),
        margin: const EdgeInsets.symmetric(vertical: 6),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.disabled)) {
              return LycosaColors.border;
            }
            if (states.contains(WidgetState.hovered) ||
                states.contains(WidgetState.pressed)) {
              return LycosaColors.primaryHover;
            }
            return LycosaColors.primary;
          }),
          foregroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.disabled)
                ? LycosaColors.textSecondary
                : LycosaColors.onPrimary,
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
                ? LycosaColors.backgroundSecondary
                : LycosaColors.background,
          ),
          foregroundColor: const WidgetStatePropertyAll(
            LycosaColors.textPrimary,
          ),
          side: const WidgetStatePropertyAll(
            BorderSide(color: LycosaColors.border),
          ),
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
          foregroundColor: const WidgetStatePropertyAll(
            LycosaColors.textPrimary,
          ),
          overlayColor: WidgetStatePropertyAll(
            LycosaColors.primaryLight.withValues(alpha: 0.5),
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
        fillColor: LycosaColors.backgroundSecondary,
        hoverColor: LycosaColors.backgroundSecondary,
        border: inputBorder(LycosaColors.border),
        enabledBorder: inputBorder(LycosaColors.border),
        focusedBorder: inputBorder(LycosaColors.primary, 2),
        errorBorder: inputBorder(LycosaColors.error),
        focusedErrorBorder: inputBorder(LycosaColors.error, 2),
        labelStyle: const TextStyle(color: LycosaColors.textSecondary),
        hintStyle: const TextStyle(color: LycosaColors.textSecondary),
        isDense: true,
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: LycosaColors.background,
        indicatorColor: LycosaColors.primary,
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
        selectedIconTheme: const IconThemeData(
          color: LycosaColors.onPrimary,
          size: 22,
        ),
        unselectedIconTheme: const IconThemeData(
          color: LycosaColors.textSecondary,
          size: 22,
        ),
        selectedLabelTextStyle: textTheme.labelMedium!.copyWith(
          color: LycosaColors.textPrimary,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelTextStyle: textTheme.labelMedium!.copyWith(
          color: LycosaColors.textSecondary,
        ),
        useIndicator: true,
        minWidth: 76,
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        selectedTileColor: LycosaColors.primaryLight,
        selectedColor: LycosaColors.textPrimary,
        iconColor: LycosaColors.textSecondary,
      ),
      chipTheme: ChipThemeData(
        backgroundColor: LycosaColors.background,
        side: const BorderSide(color: LycosaColors.border),
        shape: const StadiumBorder(),
        labelStyle: textTheme.labelMedium?.copyWith(
          color: LycosaColors.textPrimary,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: LycosaColors.background,
        surfaceTintColor: Colors.transparent,
        elevation: 12,
        shadowColor: Colors.black.withValues(alpha: 0.15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: LycosaColors.border),
        ),
        titleTextStyle: textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          fontSize: 20,
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: LycosaColors.textPrimary,
        contentTextStyle: const TextStyle(color: Colors.white),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        elevation: 4,
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: LycosaColors.textPrimary,
          borderRadius: BorderRadius.circular(8),
        ),
        textStyle: const TextStyle(color: Colors.white, fontSize: 12),
        waitDuration: const Duration(milliseconds: 400),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: LycosaColors.loading,
        linearTrackColor: LycosaColors.primaryLight,
        circularTrackColor: LycosaColors.primaryLight,
      ),
      dividerTheme: const DividerThemeData(
        color: LycosaColors.border,
        thickness: 1,
        space: 1,
      ),
      dataTableTheme: DataTableThemeData(
        headingTextStyle: textTheme.labelMedium?.copyWith(
          color: LycosaColors.textSecondary,
          fontWeight: FontWeight.w600,
        ),
        headingRowColor: const WidgetStatePropertyAll(
          LycosaColors.backgroundSecondary,
        ),
        dataRowColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.hovered)
              ? LycosaColors.backgroundSecondary
              : Colors.transparent,
        ),
        dividerThickness: 1,
      ),
      dropdownMenuTheme: DropdownMenuThemeData(
        menuStyle: MenuStyle(
          backgroundColor: const WidgetStatePropertyAll(
            LycosaColors.background,
          ),
          surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
          shape: WidgetStatePropertyAll(
            RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: const BorderSide(color: LycosaColors.border),
            ),
          ),
        ),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: LycosaColors.background,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: LycosaColors.border),
        ),
        elevation: 6,
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? LycosaColors.primary
              : Colors.transparent,
        ),
        checkColor: const WidgetStatePropertyAll(LycosaColors.onPrimary),
        side: const BorderSide(color: LycosaColors.border, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(5)),
      ),
      expansionTileTheme: const ExpansionTileThemeData(
        shape: RoundedRectangleBorder(side: BorderSide.none),
        collapsedShape: RoundedRectangleBorder(side: BorderSide.none),
        iconColor: LycosaColors.textSecondary,
        collapsedIconColor: LycosaColors.textSecondary,
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.selected)
                ? LycosaColors.primaryLight
                : LycosaColors.background,
          ),
          foregroundColor: WidgetStateProperty.resolveWith(
            (states) => states.contains(WidgetState.selected)
                ? LycosaColors.onPrimary
                : LycosaColors.textSecondary,
          ),
          side: const WidgetStatePropertyAll(
            BorderSide(color: LycosaColors.border),
          ),
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
      tabBarTheme: const TabBarThemeData(
        labelColor: LycosaColors.textPrimary,
        unselectedLabelColor: LycosaColors.textSecondary,
        indicatorColor: LycosaColors.primary,
        dividerColor: LycosaColors.border,
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStatePropertyAll(
          LycosaColors.textSecondary.withValues(alpha: 0.35),
        ),
        radius: const Radius.circular(8),
      ),
    );
  }

  /// Branded dark variant (kept in sync with the light palette); the app
  /// currently ships light-first per the brand spec.
  static ThemeData dark() {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: LycosaColors.primary,
          brightness: Brightness.dark,
        ).copyWith(
          primary: LycosaColors.primary,
          onPrimary: LycosaColors.onPrimary,
          error: LycosaColors.error,
          surfaceTint: Colors.transparent,
        );
    return ThemeData(colorScheme: scheme, brightness: Brightness.dark);
  }
}
