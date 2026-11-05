#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowsInstallTarget {
    pub ntfs_drive: String,
    pub virtual_disk_gb: u32,
}

impl WindowsInstallTarget {
    pub fn new(drive: &str, size_gb: u32) -> Self {
        Self { ntfs_drive: drive.into(), virtual_disk_gb: size_gb }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_windows_install_target_aaa() {
        // Arrange & Act
        let target = WindowsInstallTarget::new("C:/", 32);
        // Assert
        assert_eq!(target.ntfs_drive, "C:/");
        assert_eq!(target.virtual_disk_gb, 32);
    }
}
