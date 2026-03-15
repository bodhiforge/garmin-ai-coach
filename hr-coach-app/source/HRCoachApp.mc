using Toybox.Application;
using Toybox.WatchUi;

var gView = null;

class HRCoachApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function getInitialView() {
        gView = new HRCoachView();
        return [gView, new HRCoachDelegate()];
    }
}
