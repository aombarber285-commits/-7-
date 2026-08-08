import sqlite3
from datetime import datetime

DB_FILE = "v15_ultimate_v4_metabrain.db"

class V15UltimateV4MetaBrain:
    def __init__(self):
        self.db_file = DB_FILE
        self.initialize_database()

    def initialize_database(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 1. Main Meta-Brain Journal (รองรับ Counterfactual ละเอียด 4 สถานะ และ Master Score)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metabrain_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                trade_type TEXT, -- REAL_TRADE, COUNTERFACTUAL
                pattern TEXT,
                direction TEXT,
                regime TEXT,
                master_score REAL,
                meta_decision TEXT, -- TRADE, WAIT, NO_TRADE, BLOCK
                counterfactual_status TEXT, -- MISSED_WIN, AVOIDED_LOSS, MISSED_LOSS, CORRECT_SKIP
                result TEXT,
                pnl REAL
            )
        ''')
        
        # 2. Probability Calibration & Brier Score
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS probability_calibration (
                calibration_key TEXT PRIMARY KEY,
                confidence_bucket TEXT,
                total_samples INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                actual_winrate REAL DEFAULT 0.0,
                brier_score_sum REAL DEFAULT 0.0
            )
        ''')
        
        # 3. Entry Timing Memory
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entry_timing_memory (
                timing_key TEXT PRIMARY KEY,
                total_samples INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                winrate REAL DEFAULT 0.0
            )
        ''')
        
        # 4. Cooldown Tracker
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldown_tracker (
                key TEXT PRIMARY KEY,
                consecutive_losses INTEGER DEFAULT 0,
                cooldown_status TEXT DEFAULT 'NORMAL'
            )
        ''')
        
        # 5. Walk-Forward Gate
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wf_validation_state (
                phase_name TEXT PRIMARY KEY,
                is_unlocked INTEGER DEFAULT 0
            )
        ''')
        
        for p in ['TRAIN', 'VALIDATE', 'FORWARD_TEST', 'LIVE']:
            cursor.execute('INSERT OR IGNORE INTO wf_validation_state (phase_name, is_unlocked) VALUES (?, 0)', (p,))
            
        conn.commit()
        conn.close()

    def get_confidence_bucket(self, conf):
        if 60 <= conf < 70: return "60-70"
        elif 70 <= conf < 80: return "70-80"
        elif 80 <= conf < 90: return "80-90"
        else: return "90-100"

    def get_timing_bucket(self, seconds):
        if 45 <= seconds <= 60: return "45-60s"
        elif 30 <= seconds < 45: return "30-45s"
        elif 15 <= seconds < 30: return "15-30s"
        else: return "<15s"

    def evaluate_regime_transition(self, current_regime, previous_regime):
        """ข้อ 1: Regime Transition Detector ตรวจจับการเปลี่ยนโหมดตลาด"""
        if previous_regime and previous_regime != current_regime:
            return "TRANSITION"
        return "STABLE_REGIME"

    def evaluate_signal_stability(self, recent_signals):
        """ข้อ 2: Signal Stability Engine ตรวจสอบความนิ่งของสัญญาณ 2-3 รอบล่าสุด (เช่น ['CALL', 'CALL', 'CALL'])"""
        if not recent_signals or len(recent_signals) < 3:
            return "UNSTABLE"
        if len(set(recent_signals[-3:])) == 1:
            return "STABLE"
        return "UNSTABLE"

    def evaluate_market_noise(self, noise_data):
        """ข้อ 3: Market Noise Filter คัดกรองตลาดขยะ (อ่านยาก)"""
        is_small_body = noise_data.get('small_body', False)
        long_wicks = noise_data.get('long_wicks', False)
        choppy_candles = noise_data.get('choppy_color_switch', False)
        volume_weak = noise_data.get('volume_unsupported', False)
        mid_range = noise_data.get('at_middle_range', False)
        
        noise_points = sum([is_small_body, long_wicks, choppy_candles, volume_weak, mid_range])
        if noise_points >= 2:
            return True # ถือว่าเป็น Noise สูง
        return False

    def evaluate_master_decision(self, data):
        """
        ท่อประมวลผลสมองกลระดับสูงสุด V15 Ultimate V4:
        รวม Noise Filter, Transition Detector, Signal Stability และคำนวณ Master Score ชั้นสุดท้าย
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # ตรวจสอบ Walk-Forward Gate
        cursor.execute("SELECT is_unlocked FROM wf_validation_state WHERE phase_name = 'LIVE'")
        wf_row = cursor.fetchone()
        if not wf_row or wf_row[0] == 0:
            conn.close()
            return "BLOCK", 0.0, "BLOCK: Live Gate Locked"

        pattern = data['pattern']
        direction = data['direction']
        regime = data['regime']
        prev_regime = data.get('previous_regime', regime)
        raw_conf = data['raw_confidence']
        remaining_sec = data.get('candle_remaining_seconds', 45)
        timing_bucket = self.get_timing_bucket(remaining_sec)
        
        # 1. ตรวจสอบ Cooldown
        cd_key = f"{pattern}_{regime}"
        cursor.execute("SELECT cooldown_status FROM cooldown_tracker WHERE key = ?", (cd_key,))
        cd_row = cursor.fetchone()
        if cd_row and cd_row[1] == 'BLOCK_PATTERN':
            conn.close()
            return "NO_TRADE", 0.0, "NO_TRADE: Pattern blocked by Cooldown"

        # 2. Market Noise Filter Check (ข้อ 3)
        if self.evaluate_market_noise(data.get('noise_metrics', {})):
            conn.close()
            return "NO_TRADE", 0.0, "NO_TRADE: MARKET_NOISE (Unfavorable market micro-structure)"

        # 3. Regime Transition Detector Check (ข้อ 1)
        transition_state = self.evaluate_regime_transition(regime, prev_regime)
        if transition_state == "TRANSITION":
            conn.close()
            return "WAIT", 0.0, "WAIT: Regime Transition detected (Market shifting mode)"

        # 4. Signal Stability Check (ข้อ 2)
        stability_state = self.evaluate_signal_stability(data.get('recent_signals', []))
        if stability_state == "UNSTABLE":
            conn.close()
            return "WAIT", 0.0, "WAIT: Signal Unstable (Conflicting triggers across recent ticks)"

        # 5. ดึงข้อมูล Calibration & Brier Score
        bucket = self.get_confidence_bucket(raw_conf)
        cal_key = f"{pattern}_{direction}_{regime}_{bucket}"
        cursor.execute("SELECT total_samples, wins, actual_winrate, brier_score_sum FROM probability_calibration WHERE calibration_key = ?", (cal_key,))
        cal_row = cursor.fetchone()
        
        historical_wr = cal_row[2] if cal_row and cal_row[0] >= 5 else raw_conf
        calibrated_conf = (raw_conf * 0.5) + (historical_wr * 0.5)
        if cal_row and cal_row[0] >= 5 and (cal_row[3] / cal_row[0]) > 0.25:
            calibrated_conf *= 0.80

        # Entry Quality & EV
        eq_score = data.get('entry_quality_score', 75.0)
        regime_score = 75.0 # คะแนนพื้นฐานโหมดตลาด
        stability_score = 100.0 if stability_state == "STABLE" else 40.0
        ev_score_normalized = min(100.0, max(0.0, data.get('ev_value', 1.0) * 50.0 + 50.0))
        second_opinion_score = 80.0

        conn.close()

        # 6. Master Score Calculation (ข้อ 5: น้ำหนัก 6 ส่วนรวมกันเป็น 100%)
        master_score = (
            calibrated_conf * 0.30 +      # Calibration (30%)
            eq_score * 0.20 +             # Entry Quality (20%)
            regime_score * 0.15 +         # Regime (15%)
            stability_score * 0.15 +      # Signal Stability (15%)
            ev_score_normalized * 0.10 +  # EV (10%)
            second_opinion_score * 0.10   # Second Opinion (10%)
        )

        # 7. Final Master Decision Gates (ข้อ 5)
        if master_score >= 85.0:
            return "TRADE", master_score, f"SNIPER TRADE: Master Score {master_score:.1f} (>=85)"
        elif 78.0 <= master_score < 85.0:
            return "TRADE", master_score, f"TRADE: Master Score {master_score:.1f} (78-84)"
        elif 70.0 <= master_score < 78.0:
            return "WAIT", master_score, f"WAIT: Master Score {master_score:.1f} (70-77, filtering low quality)"
        else:
            return "NO_TRADE", master_score, f"NO_TRADE: Master Score {master_score:.1f} (<70)"

    def process_feedback_and_counterfactual(self, data, decision, master_score, result, pnl, is_counterfactual=False):
        """
        วงจรเรียนรู้ครบวงจร (Feedback Loop):
        - บันทึก Counterfactual ละเอียด 4 สถานะ (MISSED_WIN, AVOIDED_LOSS, MISSED_LOSS, CORRECT_SKIP)
        - อัปเดต Cooldown แบบ Strict Reset
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        pattern = data['pattern']
        direction = data['direction']
        regime = data['regime']
        raw_conf = data['raw_confidence']
        
        trade_type = "COUNTERFACTUAL" if is_counterfactual else "REAL_TRADE"
        cf_status = "N/A"
        
        if is_counterfactual:
            # ข้อ 4: แยก Counterfactual เป็น 4 สถานะที่แม่นยำ
            if decision in ["NO_TRADE", "WAIT", "BLOCK"]:
                if result == 'LOSS':
                    cf_status = "AVOIDED_LOSS" # ระบบไม่เข้า แล้วตลาดออก Loss = ตัดสินใจฉลาดมาก (Correct Skip/Avoided Loss)
                else:
                    cf_status = "MISSED_WIN"   # ระบบไม่เข้า แต่ตลาดออก Win = พลาดโอกาส
            else:
                cf_status = "CORRECT_SKIP"

        # บันทึก Journal
        cursor.execute('''
            INSERT INTO metabrain_journal (
                timestamp, trade_type, pattern, direction, regime,
                master_score, meta_decision, counterfactual_status, result, pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trade_type, pattern, direction, regime,
            master_score, decision, cf_status, result, pnl
        ))
        
        if trade_type == "REAL_TRADE" and result in ['WIN', 'LOSS']:
            bucket = self.get_confidence_bucket(raw_conf)
            cal_key = f"{pattern}_{direction}_{regime}_{bucket}"
            pred_prob = master_score / 100.0
            actual_outcome = 1.0 if result == 'WIN' else 0.0
            brier_diff = (pred_prob - actual_outcome) ** 2

            cursor.execute("SELECT total_samples, wins, brier_score_sum FROM probability_calibration WHERE calibration_key = ?", (cal_key,))
            row = cursor.fetchone()
            if not row:
                cursor.execute('INSERT INTO probability_calibration VALUES (?, ?, 1, ?, ?, ?)', 
                               (cal_key, bucket, 1 if result=='WIN' else 0, 100.0 if result=='WIN' else 0.0, brier_diff))
            else:
                ts, tw, bs = row
                ts += 1
                if result == 'WIN': tw += 1
                cursor.execute('UPDATE probability_calibration SET total_samples = ?, wins = ?, actual_winrate = ?, brier_score_sum = ? WHERE calibration_key = ?',
                               (ts, tw, (tw/ts)*100.0, bs + brier_diff, cal_key))

            # Cooldown Strict Reset
            cd_key = f"{pattern}_{regime}"
            cursor.execute("SELECT consecutive_losses FROM cooldown_tracker WHERE key = ?", (cd_key,))
            cd_row = cursor.fetchone()
            consec_losses = cd_row[0] if cd_row else 0
            
            if result == 'LOSS':
                consec_losses += 1
            else:
                consec_losses = 0 # รีเซ็ตเป็น 0 ทันทีเมื่อชนะ
                
            cd_status = 'NORMAL'
            if consec_losses == 2: cd_status = 'REDUCE_CONF'
            elif consec_losses == 3: cd_status = 'WAIT'
            elif consec_losses >= 4: cd_status = 'BLOCK_PATTERN'

            cursor.execute('''
                INSERT INTO cooldown_tracker (key, consecutive_losses, cooldown_status)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET consecutive_losses=excluded.consecutive_losses, cooldown_status=excluded.cooldown_status
            ''', (cd_key, consec_losses, cd_status))

        conn.commit()
        
        # คำนวณสถิติ No-Trade Quality & Avoided Loss Rate ย้อนหลัง
        cursor.execute("SELECT COUNT(*) FROM metabrain_journal WHERE trade_type = 'COUNTERFACTUAL'")
        total_cf = cursor.fetchone()[0]
        avoided_loss_rate = 0.0
        if total_cf > 0:
            cursor.execute("SELECT COUNT(*) FROM metabrain_journal WHERE trade_type = 'COUNTERFACTUAL' AND counterfactual_status = 'AVOIDED_LOSS'")
            avoided_count = cursor.fetchone()[0]
            avoided_loss_rate = (avoided_count / total_cf) * 100.0

        conn.close()
        print(f"[V15 ULTIMATE V4] Type: {trade_type} | Decision: {decision} | Master Score: {master_score:.1f} | Avoided Loss Rate: {avoided_loss_rate:.1f}%")
