import { useMemo } from "react";
import { useReplayStore } from "../state/replayStore";
import { activeKerbContact, kerbContactText, kerbEventsForLap } from "../replay/kerbContacts";
import { cornerName } from "../replay/apexKpi";
import { registrationVariantText } from "../replay/registrationVariant";

/**
 * HUD rows for the optional Fuji analysis layers, mounted inside the lap-info
 * panel: which GPS registration file is active, and an IMU kerb-contact
 * indicator lit while the playback time is inside a contact event (strength
 * only — no left/right side, which the data cannot determine).
 */
export function HudAnalysisRows() {
  const kerbContacts = useReplayStore((state) => state.kerbContacts);
  const apexKpi = useReplayStore((state) => state.apexKpi);
  const source = useReplayStore((state) => state.gpsRegistrationSource);
  const activeLap = useReplayStore((state) => state.activeLap);
  const raceId = activeLap?.meta.race_id;
  const lapNumber = activeLap?.meta.lap;

  const events = useMemo(() => kerbEventsForLap(kerbContacts, raceId, lapNumber),
    [kerbContacts, raceId, lapNumber]);
  const variantText = activeLap?.registration ? registrationVariantText(source) : null;
  // Stable event object from the parsed file: re-renders only when the lit event changes.
  const event = useReplayStore((state) => activeKerbContact(events, state.currentTime));
  const name = event?.insidePass ? cornerName(apexKpi, event.kerb) : null;

  if (!variantText && !kerbContacts) return null;
  return (
    <>
      {variantText && (
        <div className="hud-row hud-gps-source">
          <span
            className={`hud-gps-source-text${source?.variant === "kerb" ? " hud-gps-source-text--candidate" : ""}`}
            title={source?.variant === "kerb"
              ? "IMUの縁石接触ラベルも使って推定した7/30限定の候補（選択式・未検証）。?gps=kerb を外すと標準に戻ります"
              : source?.fellBack
                ? "?gps=kerb が指定されましたが、このレースには縁石接触候補のファイルがないため標準の補正を使っています"
                : "コース端との整合から推定した標準のGPS補正（gps_registration.json）"}
          >
            {variantText}
          </span>
        </div>
      )}
      {kerbContacts && (
        <div className="hud-row hud-kerb">
          <span
            className="hud-label"
            title="ロガー内蔵IMUのロールレート振動（0.2秒RMS）による縁石接触の推定。1.5以上=接触あり（中1.5〜3／強3以上）、1.0〜1.5=判定保留・弱（deg/s）。左右は判定していません"
          >
            KERB
          </span>
          <span
            className={`hud-kerb-pill${event ? ` hud-kerb-pill--${event.bin}` : ""}`}
            title={event ? `振動ピーク ${event.peak.toFixed(2)} deg/s（${event.t0.toFixed(2)}〜${event.t1.toFixed(2)} s）` : undefined}
          >
            {kerbContactText(event)}
          </span>
          {name && <span className="hud-kerb-corner">{name}</span>}
        </div>
      )}
    </>
  );
}
