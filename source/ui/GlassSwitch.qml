import QtQuick
import QtQuick.Controls

Switch {
    id: toggle
    property bool reducedMotion: bridge.state.reducedMotion
    padding: 4
    implicitWidth: 70; implicitHeight: 42
    hoverEnabled: true
    indicator: Rectangle {
        x: 4; y: (toggle.height - height) / 2
        width: 62; height: 36; radius: 18
        color: toggle.checked ? "#007aff" : "#cbd6e7"
        border.width: toggle.visualFocus ? 2 : 0
        border.color: "#405d88"
        Behavior on color { ColorAnimation { duration: toggle.reducedMotion ? 0 : 180 } }
        Rectangle {
            x: toggle.checked ? 29 : 3
            y: 3; width: 30; height: 30; radius: 15
            color: "white"
            Behavior on x { NumberAnimation { duration: toggle.reducedMotion ? 0 : 240; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Item {}
}
