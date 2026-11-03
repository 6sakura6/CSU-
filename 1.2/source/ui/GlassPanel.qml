import QtQuick
import QtQuick.Effects

Rectangle {
    id: panel
    property Item backdrop: null
    property real glassOpacity: 0.68
    color: "transparent"
    radius: 26
    ShaderEffectSource {
        id: sample
        sourceItem: panel.backdrop
        sourceRect: panel.backdrop ? Qt.rect(panel.mapToItem(panel.backdrop, 0, 0).x,
                     panel.mapToItem(panel.backdrop, 0, 0).y, panel.width, panel.height) : Qt.rect(0, 0, 1, 1)
        textureSize: Qt.size(Math.max(1, panel.width), Math.max(1, panel.height))
        live: true
        visible: false
    }
    Rectangle {
        id: mask
        width: panel.width; height: panel.height
        radius: panel.radius; color: "white"
        layer.enabled: true; visible: false
    }
    MultiEffect {
        z: -2
        anchors.fill: parent
        source: sample
        visible: panel.backdrop !== null
        blurEnabled: true; blur: 0.45; blurMax: 32
        maskEnabled: true; maskSource: mask
        autoPaddingEnabled: false
    }
    Rectangle {
        z: -1
        anchors.fill: parent
        radius: panel.radius
        color: Qt.rgba(1, 1, 1, panel.glassOpacity)
        border.width: 1
        border.color: "#dfffffff"
    }
}
