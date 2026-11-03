import QtQuick
import QtQuick.Shapes

Item {
    id: ring
    property bool busy: false
    property bool completed: false
    property bool reducedMotion: false
    width: 160; height: 160
    Rectangle {
        anchors.fill: parent; radius: width / 2
        color: "#10ffffff"; border.width: 7; border.color: "#10007aff"
    }
    Shape {
        anchors.fill: parent
        rotation: 0
        ShapePath {
            strokeWidth: 7; strokeColor: ring.completed ? "#13b981" : "#428fff"
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.width / 2; centerY: ring.height / 2
                radiusX: ring.width / 2 - 4; radiusY: ring.height / 2 - 4
                startAngle: -90; sweepAngle: ring.completed ? 360 : 76
            }
        }
        RotationAnimation on rotation {
            from: 0; to: 360; duration: 1200; loops: Animation.Infinite
            running: ring.busy && !ring.reducedMotion
        }
    }
    IconImage {
        name: ring.completed ? "check" : "clock"; tint: ring.completed ? "#13b981" : "#536d95"
        width: 42; height: 42; anchors.centerIn: parent
    }
}
