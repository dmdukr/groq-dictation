# Override contrib hook that fails with webrtcvad-wheels package
# webrtcvad-wheels provides the webrtcvad module but has different metadata
hiddenimports = ["webrtcvad"]
