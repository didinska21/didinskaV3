// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title DelegateWallet - Auto-forwarder native coin ke wallet penampung
/// @notice Saat menerima native coin, kontrak akan langsung meneruskan seluruh balance ke `sink`.
/// @dev Tanpa dependensi eksternal; ada ReentrancyGuard sederhana.
contract DelegateWallet {
    // ============ Events ============
    event SinkUpdated(address indexed oldSink, address indexed newSink);
    event Paused(address indexed by, bool paused);
    event Forwarded(address indexed to, uint256 amount);
    event Received(address indexed from, uint256 amount);

    // ============ Storage ============
    address public owner;
    address public sink;
    bool    public paused;

    // Reentrancy guard
    uint256 private _guard;

    // ============ Modifiers ============
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier nonReentrant() {
        require(_guard == 0, "Reentrancy");
        _guard = 1;
        _;
        _guard = 0;
    }

    modifier notPaused() {
        require(!paused, "Paused");
        _;
    }

    // ============ Constructor ============
    constructor(address _sink) {
        require(_sink != address(0), "Invalid sink");
        owner = msg.sender;
        sink = _sink;
        _guard = 0;
    }

    // ============ Admin ============
    function setSink(address _sink) external onlyOwner {
        require(_sink != address(0), "Invalid sink");
        address old = sink;
        sink = _sink;
        emit SinkUpdated(old, _sink);
    }

    function setPaused(bool _paused) external onlyOwner {
        paused = _paused;
        emit Paused(msg.sender, _paused);
    }

    /// @notice Tarik saldo (kalau ada sisa) ke sink secara manual
    function sweep() public nonReentrant {
        uint256 bal = address(this).balance;
        if (bal == 0) return;
        (bool ok, ) = payable(sink).call{value: bal}("");
        require(ok, "Forward failed");
        emit Forwarded(sink, bal);
    }

    /// @notice Tarik token ERC-20 yang nyasar (opsional)
    function sweepToken(address token) external onlyOwner nonReentrant {
        (bool s1, bytes memory d1) = token.staticcall(abi.encodeWithSignature("balanceOf(address)", address(this)));
        require(s1 && d1.length >= 32, "balanceOf fail");
        uint256 bal = abi.decode(d1, (uint256));
        if (bal == 0) return;
        (bool s2, ) = token.call(abi.encodeWithSignature("transfer(address,uint256)", sink, bal));
        require(s2, "transfer fail");
    }

    // ============ Receive/Fallback ============
    receive() external payable notPaused nonReentrant {
        emit Received(msg.sender, msg.value);
        // forward seluruh balance (habisin saldo)
        uint256 bal = address(this).balance;
        if (bal == 0) return;
        (bool ok, ) = payable(sink).call{value: bal}("");
        require(ok, "Forward failed");
        emit Forwarded(sink, bal);
    }

    fallback() external payable notPaused nonReentrant {
        if (msg.value > 0) {
            emit Received(msg.sender, msg.value);
            uint256 bal = address(this).balance;
            (bool ok, ) = payable(sink).call{value: bal}("");
            require(ok, "Forward failed");
            emit Forwarded(sink, bal);
        }
    }
}
