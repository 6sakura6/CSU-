import QtQuick
import QtQuick.Controls

SpinBox {
    id: control
    implicitWidth: 142; implicitHeight: 46
    leftPadding: 40; rightPadding: 40
    editable: true
    background: Rectangle { color: "#f5f8fd"; radius: 12; border.color: control.activeFocus ? "#007aff" : "#dbe5f2"; border.width: control.activeFocus ? 2 : 1 }
    contentItem: TextInput {
        text: control.textFromValue(control.value, control.locale)
        color: "#345077"; font.family: "Segoe UI"; font.pixelSize: 18
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        readOnly: !control.editable; validator: control.validator
        inputMethodHints: Qt.ImhDigitsOnly
        selectByMouse: true
        onEditingFinished: control.value = control.valueFromText(text, control.locale)
    }
    up.indicator: Rectangle {
        x: control.width - width; width: 36; height: control.height; radius: 12
        color: control.up.pressed ? "#cfe4ff" : "#e8f1fc"
        IconImage { anchors.centerIn: parent; name: "chevron"; rotation: -90; width: 18; height: 18 }
    }
    down.indicator: Rectangle {
        width: 36; height: control.height; radius: 12
        color: control.down.pressed ? "#cfe4ff" : "#e8f1fc"
        IconImage { anchors.centerIn: parent; name: "chevron"; rotation: 90; width: 18; height: 18 }
    }
}
