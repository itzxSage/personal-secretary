import Testing
@testable import SecretaryContract

@Test("accepts matching contract version")
func acceptsMatchingContractVersion() {
    // Given: the client and server advertise the generated contract version.
    let serverVersion = SecretaryContractVersion.current.rawValue

    // When: compatibility is evaluated.
    let result = ContractCompatibility.evaluate(serverVersion: serverVersion)

    // Then: the client accepts the service.
    #expect(result == .compatible)
}

@Test("rejects mismatched contract version")
func rejectsMismatchedContractVersion() {
    // Given: the service advertises a version different from the client.
    let serverVersion = "999.0.0"

    // When: compatibility is evaluated.
    let result = ContractCompatibility.evaluate(serverVersion: serverVersion)

    // Then: the mismatch preserves both versions for a typed UI decision.
    #expect(
        result == .incompatible(
            expected: SecretaryContractVersion.current.rawValue,
            received: serverVersion
        )
    )
}

@Test("rejects malformed contract version")
func rejectsMalformedContractVersion() {
    // Given: the service advertises malformed contract input.
    let serverVersion = "not-a-version"

    // When: compatibility is evaluated.
    let result = ContractCompatibility.evaluate(serverVersion: serverVersion)

    // Then: malformed input is represented explicitly.
    #expect(result == .malformed(received: serverVersion))
}
