import XCTest
@testable import CactusApp

final class JarvisAppTests: XCTestCase {
    func testHeuristicParserStripsWakeWordAndBuildsTask() async {
        let parser = HeuristicTaskParsingService()

        let task = await parser.parseTask(from: "Cactus please send the weekly investor update")

        XCTAssertEqual(task.status, .pending)
        XCTAssertEqual(task.taskName, "Send the weekly investor update")
        XCTAssertEqual(task.spokenConfirmation, "Got it. Task: Send the weekly investor update.")
    }

    func testHeuristicParserFallsBackForEmptyTranscript() async {
        let parser = HeuristicTaskParsingService()

        let task = await parser.parseTask(from: "cactus")

        XCTAssertEqual(task.taskName, "Follow up on the latest request")
    }
}
