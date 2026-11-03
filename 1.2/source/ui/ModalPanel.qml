import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Dialog {
    id: dialog
    property bool reducedMotion: false
    property string description: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(600, parent.width - 48)
    modal: true; focus: true
    padding: 28
    closePolicy: Popup.CloseOnEscape
    background: GlassPanel { glassOpacity: 0.97; radius: 26 }
    header: Column {
        padding: 28; bottomPadding: 8
        spacing: 10
        AppText { text: dialog.title; font.pixelSize: 25; font.weight: Font.DemiBold }
        AppText {
            text: dialog.description
            color: "#62799c"; font.pixelSize: 14
            width: dialog.width - 56
            wrapMode: Text.Wrap; elide: Text.ElideNone
        }
    }
    Overlay.modal: Rectangle {
        color: "#603d5478"
        radius: Window.window ? Window.window.frameRadius : 24
        antialiasing: true
    }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: dialog.reducedMotion ? 0 : 190 }
            NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: dialog.reducedMotion ? 0 : 240; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: dialog.reducedMotion ? 0 : 140 }
    }
}
