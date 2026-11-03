import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: actions
    default property alias buttons: row.data
    padding: 28
    topPadding: 12; bottomPadding: 24
    implicitHeight: row.implicitHeight + topPadding + bottomPadding
    contentItem: RowLayout { id: row; spacing: 10 }
}
