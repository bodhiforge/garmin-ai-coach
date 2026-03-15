using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.UserProfile;
using Toybox.Activity;
using Toybox.Lang;

// States
enum {
    STATE_ACTIVE,    // Doing a set (HR rising)
    STATE_RESTING,   // Resting between sets (HR dropping)
    STATE_READY      // HR reached target, ready for next set
}

// Modes
enum {
    MODE_STRENGTH,
    MODE_HYPERTROPHY,
    MODE_ENDURANCE
}

class HRRestFieldView extends WatchUi.DataField {

    hidden var currentHR = 0;
    hidden var targetHR = 0;
    hidden var state = STATE_ACTIVE;
    hidden var mode = MODE_STRENGTH;
    hidden var restTimerSec = 0;
    hidden var lastComputeTime = 0;
    hidden var peakHR = 0;           // Peak HR in current set
    hidden var vibeTriggered = false; // Prevent repeated vibes
    hidden var approachVibeTriggered = false;

    // Absolute target HR for each mode (bpm)
    hidden var strengthTarget = 120;
    hidden var hypertrophyTarget = 140;
    hidden var enduranceTarget = 150;

    // Thresholds for state detection
    hidden const HR_RISE_THRESHOLD = 8;   // HR rise to detect set start
    hidden const HR_DROP_THRESHOLD = 5;   // HR drop from peak to detect rest start
    hidden const APPROACH_BPM = 5;        // Vibrate when within 5bpm of target

    function initialize() {
        DataField.initialize();
        loadSettings();
    }

    function loadSettings() {
        var props = Application.Properties;
        mode = props.getValue("mode");
        strengthTarget = props.getValue("strengthTarget");
        hypertrophyTarget = props.getValue("hypertrophyTarget");
        enduranceTarget = props.getValue("enduranceTarget");
        updateTargetHR();
    }

    function updateTargetHR() {
        if (mode == MODE_HYPERTROPHY) {
            targetHR = hypertrophyTarget;
        } else if (mode == MODE_ENDURANCE) {
            targetHR = enduranceTarget;
        } else {
            targetHR = strengthTarget;
        }
    }

    // Called every second during activity
    function compute(info as Activity.Info) as Void {
        // Get current HR from activity info
        if (info has :currentHeartRate && info.currentHeartRate != null) {
            currentHR = info.currentHeartRate;
        }

        // Track time for rest timer
        if (info has :timerTime && info.timerTime != null) {
            var now = info.timerTime / 1000; // ms to seconds
            if (lastComputeTime > 0 && state == STATE_RESTING) {
                restTimerSec += (now - lastComputeTime);
            }
            lastComputeTime = now;
        }

        updateState();
    }

    function updateState() {
        if (currentHR == 0) {
            return;
        }

        if (state == STATE_ACTIVE) {
            // Track peak HR during set
            if (currentHR > peakHR) {
                peakHR = currentHR;
            }
            // Detect transition to resting: HR starts dropping from peak
            if (peakHR > targetHR && (peakHR - currentHR) >= HR_DROP_THRESHOLD) {
                state = STATE_RESTING;
                restTimerSec = 0;
                vibeTriggered = false;
                approachVibeTriggered = false;
            }
        } else if (state == STATE_RESTING) {
            // Approaching target: short vibration
            if (!approachVibeTriggered && currentHR <= targetHR + APPROACH_BPM && currentHR > targetHR) {
                doApproachVibe();
                approachVibeTriggered = true;
            }
            // Reached target: strong vibration
            if (currentHR <= targetHR) {
                state = STATE_READY;
                if (!vibeTriggered) {
                    doReadyVibe();
                    vibeTriggered = true;
                }
            }
        } else if (state == STATE_READY) {
            // Detect new set start: HR rising significantly
            if (currentHR > targetHR + HR_RISE_THRESHOLD) {
                state = STATE_ACTIVE;
                peakHR = currentHR;
                restTimerSec = 0;
            }
        }
    }

    function doApproachVibe() {
        if (Toybox.Attention has :vibrate) {
            var vibeData = [
                new Toybox.Attention.VibeProfile(40, 300)
            ];
            Toybox.Attention.vibrate(vibeData);
        }
    }

    function doReadyVibe() {
        if (Toybox.Attention has :vibrate) {
            var vibeData = [
                new Toybox.Attention.VibeProfile(100, 500),
                new Toybox.Attention.VibeProfile(0, 200),
                new Toybox.Attention.VibeProfile(100, 500),
                new Toybox.Attention.VibeProfile(0, 200),
                new Toybox.Attention.VibeProfile(100, 500)
            ];
            Toybox.Attention.vibrate(vibeData);
        }
    }

    // Called to draw the data field
    function onUpdate(dc as Graphics.Dc) as Void {
        var bgColor = getBackgroundColor();
        var fgColor = (bgColor == Graphics.COLOR_BLACK) ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK;

        var width = dc.getWidth();
        var height = dc.getHeight();

        // Background
        dc.setColor(bgColor, bgColor);
        dc.fillRectangle(0, 0, width, height);

        // State-based accent color
        var accentColor = Graphics.COLOR_YELLOW;
        if (state == STATE_READY) {
            accentColor = Graphics.COLOR_GREEN;
        } else if (state == STATE_ACTIVE) {
            accentColor = Graphics.COLOR_RED;
        }

        // Status bar at top
        dc.setColor(accentColor, accentColor);
        dc.fillRectangle(0, 0, width, 4);

        // Current HR — large centered
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(
            width / 2,
            height * 0.15,
            Graphics.FONT_NUMBER_HOT,
            currentHR.format("%d"),
            Graphics.TEXT_JUSTIFY_CENTER
        );

        // Heart icon + "bpm" label
        dc.setColor(accentColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(
            width / 2,
            height * 0.50,
            Graphics.FONT_XTINY,
            getHeartLabel(),
            Graphics.TEXT_JUSTIFY_CENTER
        );

        // Target HR
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(
            width / 2,
            height * 0.62,
            Graphics.FONT_SMALL,
            "Target: " + targetHR.format("%d"),
            Graphics.TEXT_JUSTIFY_CENTER
        );

        // Status + rest timer
        dc.setColor(accentColor, Graphics.COLOR_TRANSPARENT);
        var statusText = getStatusText();
        if (state == STATE_RESTING || state == STATE_READY) {
            statusText = statusText + "  " + formatTime(restTimerSec);
        }
        dc.drawText(
            width / 2,
            height * 0.78,
            Graphics.FONT_SMALL,
            statusText,
            Graphics.TEXT_JUSTIFY_CENTER
        );

        // Mode indicator at bottom
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(
            width / 2,
            height * 0.90,
            Graphics.FONT_XTINY,
            getModeText(),
            Graphics.TEXT_JUSTIFY_CENTER
        );
    }

    function getHeartLabel() {
        if (state == STATE_RESTING) {
            var diff = currentHR - targetHR;
            if (diff > 0) {
                return "-" + diff.format("%d") + " bpm to go";
            }
            return "bpm";
        }
        return "bpm";
    }

    function getStatusText() {
        if (state == STATE_ACTIVE) {
            return "ACTIVE";
        } else if (state == STATE_RESTING) {
            return "RESTING";
        } else {
            return "GO!";
        }
    }

    function getModeText() {
        if (mode == MODE_STRENGTH) {
            return "STR";
        } else if (mode == MODE_HYPERTROPHY) {
            return "HYP";
        } else {
            return "END";
        }
    }

    function formatTime(seconds) {
        var min = (seconds / 60).toNumber();
        var sec = (seconds % 60).toNumber();
        return min.format("%d") + ":" + sec.format("%02d");
    }
}
