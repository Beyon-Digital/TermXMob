import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";

import { ThemeProvider, useAppTheme } from "@/hooks/use-app-theme";

function ThemedStack() {
  const { theme } = useAppTheme();
  return (
    <>
      <StatusBar style={theme.kind === "dark" ? "light" : "dark"} />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: theme.ui.background },
          animation: "fade",
        }}
      />
    </>
  );
}

export default function Layout() {
  return (
    <ThemeProvider>
      <ThemedStack />
    </ThemeProvider>
  );
}
