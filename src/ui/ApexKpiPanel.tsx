import { useMemo } from "react";
import { useReplayStore } from "../state/replayStore";
import {
  apexKpiRowsForLap,
  cornerName,
  formatDelta,
  formatMeters,
  labelCheckText,
} from "../replay/apexKpi";
import {
  PASS_SILENT_MAX_PEAK,
  PASS_STRONG_MIN_PEAK,
  activePass,
  passContactDetail,
  passContactText,
  passLabelsForLap,
} from "../replay/kerbContacts";
import { formatLapClock } from "../replay/lapClock";

/** Column header tooltip: class words and strength bins are separate scales. */
const CONTACT_HEADER_TITLE =
  `IMUの振動による判定：接触あり（${PASS_STRONG_MIN_PEAK} deg/s以上）／判定保留（${PASS_SILENT_MAX_PEAK}〜${PASS_STRONG_MIN_PEAK}）／` +
  `接触なし（${PASS_SILENT_MAX_PEAK}以下）。「・」の後ろは振動の強さ：弱1.0〜1.5／中1.5〜3／強3以上`;

/**
 * Per-corner clipping-point KPI of the active lap (apex_kpi.json). Absolute
 * gaps carry ±0.7 m; the delta to the session median of the same corner is
 * the primary comparison. Clicking a row seeks to that clipping point.
 */
export function ApexKpiPanel() {
  const apexKpi = useReplayStore((state) => state.apexKpi);
  const kerbContacts = useReplayStore((state) => state.kerbContacts);
  const source = useReplayStore((state) => state.gpsRegistrationSource);
  const activeLap = useReplayStore((state) => state.activeLap);
  const show = useReplayStore((state) => state.showApexKpiPanel);
  const toggle = useReplayStore((state) => state.toggleApexKpiPanel);
  const seek = useReplayStore((state) => state.seek);
  const raceId = activeLap?.meta.race_id;
  const lapNumber = activeLap?.meta.lap;

  const rows = useMemo(() => apexKpiRowsForLap(apexKpi, raceId, lapNumber), [apexKpi, raceId, lapNumber]);
  const passes = useMemo(() => passLabelsForLap(kerbContacts, raceId, lapNumber), [kerbContacts, raceId, lapNumber]);
  // Kerb whose pass window holds the playback time (a string: re-renders only on change).
  const current = useReplayStore((state) => activePass(passes, state.currentTime)?.kerb ?? null);

  if (!apexKpi || rows.length === 0) return null;
  const unc = apexKpi.absoluteUncertaintyMeters.toFixed(1);

  return (
    <section
      className={`apex-kpi-panel hud-panel${show ? "" : " apex-kpi-panel--collapsed"}`}
      aria-label="クリッピングポイントKPI"
    >
      <div className="apex-kpi-header">
        <span className="hud-label">CLIP KPI</span>
        <span className="apex-kpi-title">クリッピングポイント・L{lapNumber}</span>
        <button
          type="button"
          className="minimap-toggle"
          onClick={toggle}
          aria-expanded={show}
          title={show ? "KPIパネルを閉じる" : "コーナーごとのクリッピングポイントKPIを開く"}
        >
          {show ? "–" : "+"}
        </button>
      </div>
      {show && (
        <>
          <p className="apex-kpi-note">
            g＝内側タイヤ外端から赤白ブロックの始まりまで（マイナス＝乗っている）。
            <strong>絶対値は±{unc}m</strong>の幅があります。
            <strong>Δ（このセッションの中央値との差）で周どうしを比べてください。</strong>
            接触はIMUの振動で、左右は判定していません。
          </p>
          {source?.variant === "kerb" && (
            <p className="apex-kpi-note apex-kpi-note--warn">
              KPIは標準のGPS補正で計算しています（いま表示中の縁石接触候補とは位置が少し異なります）。
            </p>
          )}
          <div className="apex-kpi-scroll">
            <table className="apex-kpi-table">
              <thead>
                <tr>
                  <th scope="col">コーナー</th>
                  <th scope="col" title={CONTACT_HEADER_TITLE}>接触</th>
                  <th scope="col" title={`前輪／後輪の g_block（m）。絶対値は±${unc}m`}>g前/後 ±{unc}</th>
                  <th scope="col" title="前輪 g_block のセッション中央値との差（m）。マイナス＝いつもより縁石寄り">Δg前</th>
                  <th scope="col" title="クリッピングポイント（g前が最小の地点）のコース位置（m）と、セッション中央値との差（マイナス＝手前）">クリップ位置</th>
                  <th scope="col" title="内側前輪がブロック上を走った距離（m）と中央値との差">ブロック上</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const name = cornerName(apexKpi, row.kerb) ?? row.kerb;
                  const warn = labelCheckText(row.labelCheck);
                  const d = row.deltaVsSessionMedian;
                  return (
                    <tr
                      key={row.kerb}
                      className={`apex-kpi-row${current === row.kerb ? " apex-kpi-row--active" : ""}`}
                      onClick={() => seek(row.apexT)}
                      title={`クリックでクリッピングポイントへ移動（ラップ ${formatLapClock(row.apexLapTime ?? row.apexT)}）`}
                    >
                      <th scope="row">
                        {name}
                        {warn && <span className="apex-kpi-warn" title={warn} aria-label={warn}>⚠</span>}
                      </th>
                      <td className={`apex-kpi-contact apex-kpi-contact--${row.contact?.class ?? "none"}${row.contact?.bin ? ` apex-kpi-contact--${row.contact.bin}` : ""}`}
                        title={passContactDetail(row.contact)}>
                        {passContactText(row.contact)}
                      </td>
                      <td className="apex-kpi-num">{formatMeters(row.gBlockFront)} / {formatMeters(row.gBlockRear)}</td>
                      <td className={`apex-kpi-num apex-kpi-delta${(d.gBlockFront ?? 0) < 0 ? " apex-kpi-delta--closer" : ""}`}>
                        {formatDelta(d.gBlockFront)}
                      </td>
                      <td className="apex-kpi-num">
                        {formatMeters(row.apexStation, 1)}
                        <span className="apex-kpi-sub"> {formatDelta(d.apexStation, 1)}</span>
                      </td>
                      <td className="apex-kpi-num">
                        {formatMeters(row.onBlockLengthM, 1)}
                        <span className="apex-kpi-sub"> {formatDelta(d.onBlockLengthM, 1)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
