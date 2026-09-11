import { CameraView, useCameraPermissions } from "expo-camera";
import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAppTheme } from "@/hooks/use-app-theme";
import { fetchHealth } from "@/lib/api";
import { parseConnectTarget } from "@/lib/parse-url";
import { saveConnection } from "@/lib/storage";
import { setCurrentConnection } from "@/lib/types";

export default function ScanScreen() {
  const router = useRouter();
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [permission, requestPermission] = useCameraPermissions();
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  if (!permission) return <View style={[styles.screen, { backgroundColor: ui.background }]} />;
  if (!permission.granted) {
    return (
      <SafeAreaView style={[styles.screen, { backgroundColor: ui.background }]}>
        <Text style={[styles.copy, { color: ui.text }]}>
          Camera access is needed to scan the Termx QR code.
        </Text>
        <Pressable
          style={[styles.btn, { backgroundColor: ui.accent }]}
          onPress={requestPermission}>
          <Text style={[styles.btnLabel, { color: ui.accentText }]}>Allow camera</Text>
        </Pressable>
        <Pressable onPress={() => router.back()}>
          <Text style={[styles.link, { color: ui.accent }]}>Back</Text>
        </Pressable>
      </SafeAreaView>
    );
  }

  return (
    <View style={[styles.screen, { backgroundColor: ui.background }]}>
      <CameraView
        style={StyleSheet.absoluteFill}
        barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
        onBarcodeScanned={async ({ data }) => {
          if (done) return;
          const parsed = parseConnectTarget(data);
          if (!parsed) {
            setError("Not a Termx URL");
            return;
          }
          setDone(true);
          try {
            await fetchHealth(parsed);
            setCurrentConnection(parsed);
            await saveConnection(parsed);
            router.replace("/workspace");
          } catch (err) {
            setDone(false);
            setError(err instanceof Error ? err.message : "Cannot connect");
          }
        }}
      />
      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        <Pressable onPress={() => router.back()}>
          <Text style={[styles.link, { color: ui.accent }]}>Cancel</Text>
        </Pressable>
        <Text style={[styles.copy, { color: ui.text }]}>
          Scan the QR printed by the Termx server
        </Text>
        {error ? <Text style={{ color: ui.danger }}>{error}</Text> : null}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, justifyContent: "center", padding: 24, gap: 16 },
  overlay: { position: "absolute", left: 16, right: 16, top: 0, gap: 12 },
  copy: { fontSize: 16 },
  link: { fontSize: 16, paddingVertical: 12 },
  btn: { borderRadius: 8, padding: 14, alignItems: "center" },
  btnLabel: { fontWeight: "700" },
});
