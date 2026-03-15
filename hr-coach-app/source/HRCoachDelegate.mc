using Toybox.WatchUi;
using Toybox.System;

class HRCoachDelegate extends WatchUi.BehaviorDelegate {

    function initialize() {
        BehaviorDelegate.initialize();
    }

    // START button
    function onSelect() {
        if (gView.state == STATE_IDLE) {
            // Start session
            gView.startSession();
        } else if (gView.state == STATE_ACTIVE) {
            // Manual set done — primary way to mark set completion
            gView.markSetDone();
        } else if (gView.state == STATE_RESTING || gView.state == STATE_READY) {
            // Start next set manually
            gView.startNewSet();
        } else if (gView.state == STATE_DONE) {
            System.exit();
        }
        WatchUi.requestUpdate();
        return true;
    }

    // BACK button — hold to stop session
    function onBack() {
        if (gView.state == STATE_IDLE || gView.state == STATE_DONE) {
            System.exit();
        } else {
            // Stop and save session
            gView.stopSession();
            WatchUi.requestUpdate();
        }
        return true;
    }
}
