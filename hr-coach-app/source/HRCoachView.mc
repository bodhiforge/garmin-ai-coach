using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Sensor;
using Toybox.Timer;
using Toybox.Lang;
using Toybox.Communications;
using Toybox.Application;
using Toybox.UserProfile;
using Toybox.ActivityRecording;
using Toybox.Activity;
using Toybox.FitContributor;

// States
enum {
    STATE_IDLE,      // Before starting
    STATE_ACTIVE,    // Doing a set
    STATE_RESTING,   // Between sets, waiting for AI
    STATE_READY,     // AI responded, ready for next set
    STATE_DONE       // Session ended
}

class HRCoachView extends WatchUi.View {

    // Session state (not hidden — accessed by delegate)
    var state = STATE_IDLE;
    hidden var currentHR = 0;
    hidden var peakHR = 0;
    hidden var targetHR = 120;
    hidden var setCount = 0;
    hidden var restTimerSec = 0;

    // AI response
    hidden var aiAdvice = "START to begin";
    hidden var fatiguePct = 0;

    // Session history for API calls
    hidden var sessionSets = [];

    // Config
    hidden var serverUrl = "";
    hidden var apiToken = "";

    // Internals
    hidden var timer = null;
    hidden var session = null;  // ActivityRecording session
    hidden var isRecording = false;

    // Auto-detection fallback thresholds
    hidden const HR_DROP_THRESHOLD = 10;  // Raised — manual is primary
    hidden const HR_RISE_THRESHOLD = 10;  // Auto-detect new set from READY

    function initialize() {
        View.initialize();
        loadConfig();
        setupSensor();
    }

    function loadConfig() {
        var props = Application.Properties;
        serverUrl = props.getValue("serverUrl");
        apiToken = props.getValue("apiToken");
    }

    function setupSensor() {
        Sensor.setEnabledSensors([Sensor.SENSOR_HEARTRATE]);
        Sensor.enableSensorEvents(method(:onSensor));

        timer = new Timer.Timer();
        timer.start(method(:onTimer), 1000, true);
    }

    function onSensor(sensorInfo as Sensor.Info) as Void {
        if (sensorInfo.heartRate != null) {
            currentHR = sensorInfo.heartRate;
        }
    }

    function onTimer() as Void {
        if (state == STATE_RESTING || state == STATE_READY) {
            restTimerSec++;
        }
        updateState();
        WatchUi.requestUpdate();
    }

    function updateState() {
        if (currentHR == 0 || state == STATE_IDLE || state == STATE_DONE) {
            return;
        }

        if (state == STATE_ACTIVE) {
            // Track peak HR (for AI context)
            if (currentHR > peakHR) {
                peakHR = currentHR;
            }
            // Fallback auto-detection (high threshold — manual press is primary)
            if (peakHR > 100 && (peakHR - currentHR) >= HR_DROP_THRESHOLD) {
                markSetDone();
            }
        } else if (state == STATE_RESTING) {
            if (currentHR <= targetHR) {
                state = STATE_READY;
                doReadyVibe();
            }
        } else if (state == STATE_READY) {
            // Auto-detect new set start from HR rising
            if (currentHR > targetHR + HR_RISE_THRESHOLD) {
                startNewSet();
            }
        }
    }

    // Called by delegate when user presses START during active set
    function markSetDone() {
        if (state != STATE_ACTIVE) {
            return;
        }

        setCount++;
        restTimerSec = 0;

        // Record this set
        var setData = {
            "set_number" => setCount,
            "peak_hr" => peakHR
        };
        sessionSets.add(setData);

        state = STATE_RESTING;
        aiAdvice = "Analyzing...";

        // Ask AI for coaching
        requestCoaching();

        doSetDoneVibe();
    }

    // Called by delegate when user presses START during rest/ready
    function startNewSet() {
        state = STATE_ACTIVE;
        peakHR = currentHR;
        restTimerSec = 0;
    }

    // -- Activity Recording --

    function startSession() {
        state = STATE_ACTIVE;
        peakHR = 0;
        setCount = 0;
        sessionSets = [];
        aiAdvice = "GO!";
        fatiguePct = 0;

        // Start FIT recording
        if (ActivityRecording has :createSession) {
            session = ActivityRecording.createSession({
                :name => "AI Coach",
                :sport => ActivityRecording.SPORT_TRAINING,
                :subSport => ActivityRecording.SUB_SPORT_STRENGTH_TRAINING
            });
            session.start();
            isRecording = true;
        }
    }

    function stopSession() {
        state = STATE_DONE;
        if (session != null && isRecording) {
            session.stop();
            session.save();
            isRecording = false;
        }
        aiAdvice = "Saved!";
    }

    // -- AI Communication --

    function requestCoaching() {
        var url = serverUrl + "/api/coaching";

        var setsArray = [];
        for (var i = 0; i < sessionSets.size(); i++) {
            setsArray.add(sessionSets[i]);
        }

        var body = {
            "current_hr" => currentHR,
            "session_sets" => setsArray,
            "elapsed_min" => restTimerSec > 0 ? (restTimerSec / 60.0) : 0
        };

        var options = {
            :method => Communications.HTTP_REQUEST_METHOD_POST,
            :headers => {
                "Content-Type" => Communications.REQUEST_CONTENT_TYPE_JSON,
                "X-Api-Token" => apiToken
            },
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
        };

        Communications.makeWebRequest(url, body, options, method(:onCoachingResponse));
    }

    function onCoachingResponse(responseCode as Lang.Number, data as Lang.Dictionary or Null) as Void {
        if (responseCode == 200 && data != null) {
            aiAdvice = data["advice"];
            if (data["target_hr"] != null) {
                targetHR = data["target_hr"];
            }
            if (data["fatigue_pct"] != null) {
                fatiguePct = data["fatigue_pct"];
            }
        } else {
            aiAdvice = "Offline mode";
            // Fallback: 65% of peak HR
            if (peakHR > 0) {
                targetHR = (peakHR * 0.65).toNumber();
            }
        }
        WatchUi.requestUpdate();
    }

    // -- Vibration --

    function doSetDoneVibe() {
        if (Toybox.Attention has :vibrate) {
            Toybox.Attention.vibrate([
                new Toybox.Attention.VibeProfile(50, 400)
            ]);
        }
    }

    function doReadyVibe() {
        if (Toybox.Attention has :vibrate) {
            Toybox.Attention.vibrate([
                new Toybox.Attention.VibeProfile(100, 500),
                new Toybox.Attention.VibeProfile(0, 200),
                new Toybox.Attention.VibeProfile(100, 500),
                new Toybox.Attention.VibeProfile(0, 200),
                new Toybox.Attention.VibeProfile(100, 500)
            ]);
        }
    }

    // -- Drawing --

    function onUpdate(dc as Graphics.Dc) as Void {
        var width = dc.getWidth();
        var height = dc.getHeight();
        var bgColor = Graphics.COLOR_BLACK;
        var fgColor = Graphics.COLOR_WHITE;

        // Background
        dc.setColor(bgColor, bgColor);
        dc.fillRectangle(0, 0, width, height);

        if (state == STATE_IDLE) {
            drawIdleScreen(dc, width, height, fgColor);
            return;
        }

        if (state == STATE_DONE) {
            drawDoneScreen(dc, width, height, fgColor);
            return;
        }

        // Accent color based on state
        var accentColor = Graphics.COLOR_RED;
        if (state == STATE_RESTING) {
            accentColor = Graphics.COLOR_YELLOW;
        } else if (state == STATE_READY) {
            accentColor = Graphics.COLOR_GREEN;
        }

        // Status bar
        dc.setColor(accentColor, accentColor);
        dc.fillRectangle(0, 0, width, 4);

        // Current HR — big
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.08, Graphics.FONT_NUMBER_HOT,
            currentHR.format("%d"), Graphics.TEXT_JUSTIFY_CENTER);

        // Target HR line
        dc.setColor(accentColor, Graphics.COLOR_TRANSPARENT);
        var targetText = "Target " + targetHR.format("%d");
        if (state == STATE_RESTING && currentHR > targetHR) {
            targetText = "-" + (currentHR - targetHR).format("%d") + " to go";
        }
        dc.drawText(width / 2, height * 0.42, Graphics.FONT_SMALL,
            targetText, Graphics.TEXT_JUSTIFY_CENTER);

        // AI Advice — the key feature
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.55, Graphics.FONT_SMALL,
            aiAdvice, Graphics.TEXT_JUSTIFY_CENTER);

        // Fatigue bar
        if (fatiguePct > 0) {
            var barWidth = width * 0.6;
            var barX = (width - barWidth) / 2;
            var barY = height * 0.70;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.fillRectangle(barX, barY, barWidth, 8);
            var fatColor = Graphics.COLOR_GREEN;
            if (fatiguePct > 60) {
                fatColor = Graphics.COLOR_RED;
            } else if (fatiguePct > 30) {
                fatColor = Graphics.COLOR_YELLOW;
            }
            dc.setColor(fatColor, fatColor);
            dc.fillRectangle(barX, barY, barWidth * fatiguePct / 100, 8);
        }

        // Bottom: set count + rest timer
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        var bottomText = "Set " + setCount.format("%d");
        if (state == STATE_RESTING || state == STATE_READY) {
            var min = (restTimerSec / 60).toNumber();
            var sec = (restTimerSec % 60).toNumber();
            bottomText = bottomText + "  " + min.format("%d") + ":" + sec.format("%02d");
        }
        dc.drawText(width / 2, height * 0.82, Graphics.FONT_SMALL,
            bottomText, Graphics.TEXT_JUSTIFY_CENTER);

        // State label + hint
        dc.setColor(accentColor, Graphics.COLOR_TRANSPARENT);
        var stateText = "ACTIVE  [START=done]";
        if (state == STATE_RESTING) { stateText = "RESTING"; }
        else if (state == STATE_READY) { stateText = "GO!  [START=next]"; }
        dc.drawText(width / 2, height * 0.92, Graphics.FONT_XTINY,
            stateText, Graphics.TEXT_JUSTIFY_CENTER);
    }

    function drawIdleScreen(dc, width, height, fgColor) {
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.3, Graphics.FONT_MEDIUM,
            "AI Coach", Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(width / 2, height * 0.5, Graphics.FONT_SMALL,
            "HR: " + currentHR.format("%d"), Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(width / 2, height * 0.7, Graphics.FONT_XTINY,
            "Press START", Graphics.TEXT_JUSTIFY_CENTER);
    }

    function drawDoneScreen(dc, width, height, fgColor) {
        dc.setColor(fgColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(width / 2, height * 0.3, Graphics.FONT_MEDIUM,
            "Done!", Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(width / 2, height * 0.5, Graphics.FONT_SMALL,
            setCount.format("%d") + " sets", Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(width / 2, height * 0.7, Graphics.FONT_XTINY,
            "Activity saved", Graphics.TEXT_JUSTIFY_CENTER);
    }
}
