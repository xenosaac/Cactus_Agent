import SwiftUI

struct TaskTabView: View {
    let task: TaskMetadata

    var body: some View {
        HStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .fill(task.status == .completed ? Color.green.opacity(0.9) : Color.orange.opacity(0.95))
                .frame(width: 6)
                .padding(.vertical, 16)
                .padding(.leading, 10)

            VStack(alignment: .leading, spacing: 8) {
                Text(task.status.rawValue)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.72))
                    .textCase(.uppercase)

                Text(task.taskName)
                    .font(.system(size: 16, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 16)

            Spacer(minLength: 10)
        }
        .frame(width: AppConfig.tabSize.width, height: AppConfig.tabSize.height)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.black.opacity(0.52))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                )
        )
        .shadow(color: .black.opacity(0.18), radius: 18, y: 10)
    }
}
