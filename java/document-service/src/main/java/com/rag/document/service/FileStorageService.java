package com.rag.document.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.UUID;

@Service
public class FileStorageService {

    @Value("${app.upload.dir:./uploads}")
    private String uploadDir;

    /**
     * Store uploaded file to local filesystem under uploads/{kbId}/{uuid}-{originalName}
     */
    public String storeFile(UUID knowledgeBaseId, MultipartFile file) throws IOException {
        Path kbDir = Paths.get(uploadDir, knowledgeBaseId.toString());
        Files.createDirectories(kbDir);

        String storedName = UUID.randomUUID().toString() + "-" + file.getOriginalFilename();
        Path targetPath = kbDir.resolve(storedName);
        Files.copy(file.getInputStream(), targetPath, StandardCopyOption.REPLACE_EXISTING);

        return targetPath.toAbsolutePath().toString();
    }

    /**
     * Delete all files under a knowledge base directory
     */
    public void deleteKnowledgeBaseFiles(UUID knowledgeBaseId) throws IOException {
        Path kbDir = Paths.get(uploadDir, knowledgeBaseId.toString());
        if (Files.exists(kbDir)) {
            try (var stream = Files.walk(kbDir)) {
                stream.sorted((a, b) -> b.compareTo(a))
                      .forEach(p -> {
                          try { Files.deleteIfExists(p); } catch (IOException ignored) {}
                      });
            }
        }
    }

    /**
     * Delete a single file by its path
     */
    public void deleteFile(String filePath) {
        if (filePath == null || filePath.isBlank()) return;
        try {
            Path path = Paths.get(filePath);
            Files.deleteIfExists(path);
        } catch (IOException e) {
            // Log but don't propagate - file may already be deleted
            System.err.println("Failed to delete file: " + filePath + " - " + e.getMessage());
        }
    }

    /**
     * Extract file extension from original filename
     */
    public String getFileExtension(String filename) {
        if (filename == null || !filename.contains(".")) return "";
        return filename.substring(filename.lastIndexOf('.') + 1).toLowerCase();
    }
}