using Toybox.Application;

class HRRestFieldApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function getInitialView() {
        return [new HRRestFieldView()];
    }
}
